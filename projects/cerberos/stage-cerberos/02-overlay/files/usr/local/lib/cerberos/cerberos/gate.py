"""The gate: one Ollama- and OpenAI-compatible API in front of every head (model server).

Apps that expect Ollama on localhost:11434 (Open WebUI, AnythingLLM, llm,
aider, Continue...) or an OpenAI-style /v1 endpoint all talk to the gate. It
lists every model from every head and routes each request by model name:

    chatbots/gemma          catalog alias -> the head and model in catalog.toml
    gemma3:1b               raw name      -> whichever head has it (npu, cpu, remote)
    gemma3:1b@cpu           forced head

Translation is done where a head can't speak the client's dialect: OpenAI
requests to the NPU head (hailo-ollama) become Ollama requests, and Ollama
requests to an OpenAI-only remote head become OpenAI requests.

Standard library only.
"""

import json
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import HEADS, Catalog, head_urls, read_conf

UPSTREAM_TIMEOUT = 900


def log(msg):
    print(f"[gate] {msg}", file=sys.stderr, flush=True)


# --- upstream plumbing ----------------------------------------------------------

class Upstream:
    def __init__(self, conf):
        self.urls = head_urls(conf)
        self.remote_openai = conf.get("CERBEROS_REMOTE_API", "ollama").lower() == "openai"
        self.remote_key = conf.get("CERBEROS_REMOTE_KEY", "")

    def is_openai(self, head):
        return head == "remote" and self.remote_openai

    def request(self, head, path, body=None, method=None, timeout=UPSTREAM_TIMEOUT):
        """Open a request to a head. Returns the response (caller closes it).
        Raises urllib.error.HTTPError / URLError like urlopen."""
        url = self.urls[head] + path
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if head == "remote" and self.remote_key:
            headers["Authorization"] = f"Bearer {self.remote_key}"
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method or ("POST" if data else "GET"))
        return urllib.request.urlopen(req, timeout=timeout)

    def get_json(self, head, path, timeout=3):
        with self.request(head, path, timeout=timeout) as r:
            return json.load(r)


class Router:
    """Knows which head has which model. Lists are cached briefly."""

    TTL = 8

    def __init__(self, conf, catalog, upstream):
        self.conf = conf
        self.catalog = catalog
        self.up = upstream
        self._lock = threading.Lock()
        self._tags = {}        # head -> list of Ollama-style model dicts
        self._tags_at = 0.0
        self._npu_avail = set()
        self._npu_avail_at = 0.0

    def head_models(self, refresh=False):
        with self._lock:
            if not refresh and time.monotonic() - self._tags_at < self.TTL:
                return self._tags
        tags = {}
        for head in self.up.urls:
            try:
                if self.up.is_openai(head):
                    data = self.up.get_json(head, "/v1/models")
                    tags[head] = [_tag_entry(m["id"]) for m in data.get("data", [])]
                else:
                    data = self.up.get_json(head, "/api/tags")
                    tags[head] = [_normalise_tag(m) for m in data.get("models", [])]
            except (urllib.error.URLError, OSError, ValueError, KeyError):
                tags[head] = None  # head down
        with self._lock:
            self._tags, self._tags_at = tags, time.monotonic()
        return tags

    def npu_available(self):
        """Models hailo-ollama can pull (not necessarily installed)."""
        if "npu" not in self.up.urls:
            return set()
        if time.monotonic() - self._npu_avail_at < 300:
            return self._npu_avail
        names = set()
        try:
            data = self.up.get_json("npu", "/hailo/v1/list", timeout=5)
            items = data.get("models", data) if isinstance(data, dict) else data
            for it in items or []:
                names.add(it if isinstance(it, str) else it.get("name", ""))
        except (urllib.error.URLError, OSError, ValueError, AttributeError):
            pass
        self._npu_avail, self._npu_avail_at = names, time.monotonic()
        return names

    def installed_on(self, head, name, tags=None):
        tags = tags if tags is not None else self.head_models()
        names = {m["name"] for m in tags.get(head) or []}
        return name in names or f"{name}:latest" in names or name.removesuffix(":latest") in names

    def resolve(self, name, for_pull=False):
        """Map a client-facing model name to (head, upstream model name)."""
        forced = None
        if "@" in name and name.rsplit("@", 1)[1] in HEADS:
            name, forced = name.rsplit("@", 1)
        entry = self.catalog.find_model(name)
        if entry:
            return forced or entry.head, entry.model
        if forced:
            return forced, name
        tags = self.head_models()
        for head in HEADS:
            if head in self.up.urls and self.installed_on(head, name, tags):
                return head, name
        if for_pull and name in self.npu_available():
            return "npu", name
        return "cpu", name

    def merged_tags(self):
        """Every model on every head, plus catalog aliases for installed ones."""
        tags = self.head_models()
        out, seen = [], set()
        for entry in self.catalog.models:
            if entry.head in tags and self.installed_on(entry.head, entry.model, tags):
                src = next((m for m in tags[entry.head]
                            if m["name"] in (entry.model, f"{entry.model}:latest")), None)
                item = dict(src or _tag_entry(entry.alias))
                item["name"] = item["model"] = entry.alias
                item.setdefault("details", {})["family"] = item.get("details", {}).get("family") or entry.head
                out.append(item)
                seen.add(entry.alias)
        for head in HEADS:
            for m in tags.get(head) or []:
                name = m["name"] if m["name"] not in seen else f"{m['name']}@{head}"
                item = dict(m, name=name, model=name)
                out.append(item)
                seen.add(name)
        return out


def _tag_entry(name):
    return {"name": name, "model": name, "modified_at": "1970-01-01T00:00:00Z",
            "size": 0, "digest": "", "details": {}}


def _normalise_tag(m):
    if isinstance(m, str):
        return _tag_entry(m)
    out = _tag_entry(m.get("name") or m.get("model") or "?")
    out.update({k: v for k, v in m.items() if v is not None})
    out["model"] = out["name"]
    return out


# --- dialect translation ----------------------------------------------------------

def _text(content):
    """OpenAI content may be a string or a list of parts."""
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


def openai_to_ollama_chat(body, model):
    opts = {}
    for src, dst in (("temperature", "temperature"), ("top_p", "top_p"), ("seed", "seed"),
                     ("max_tokens", "num_predict"), ("max_completion_tokens", "num_predict"),
                     ("frequency_penalty", "frequency_penalty"),
                     ("presence_penalty", "presence_penalty")):
        if body.get(src) is not None:
            opts[dst] = body[src]
    if body.get("stop"):
        opts["stop"] = body["stop"] if isinstance(body["stop"], list) else [body["stop"]]
    msgs = [{"role": m.get("role", "user"), "content": _text(m.get("content"))}
            for m in body.get("messages", [])]
    return {"model": model, "messages": msgs, "stream": bool(body.get("stream")), "options": opts}


def ollama_to_openai_chat(body, model, generate=False):
    if generate:
        msgs = []
        if body.get("system"):
            msgs.append({"role": "system", "content": body["system"]})
        msgs.append({"role": "user", "content": body.get("prompt", "")})
    else:
        msgs = [{"role": m.get("role", "user"), "content": m.get("content", "")}
                for m in body.get("messages", [])]
    out = {"model": model, "messages": msgs, "stream": body.get("stream", True)}
    opts = body.get("options") or {}
    for src, dst in (("temperature", "temperature"), ("top_p", "top_p"), ("seed", "seed"),
                     ("num_predict", "max_tokens"), ("stop", "stop")):
        if src in opts:
            out[dst] = opts[src]
    return out


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --- HTTP handler -------------------------------------------------------------------

class GateHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "CerberOS-gate"
    router: Router = None
    up: Upstream = None
    key = ""

    def log_message(self, fmt, *args):
        if self.server.verbose:
            log(f"{self.client_address[0]} {fmt % args}")

    # helpers
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")

    def send_json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def error(self, status, msg, openai=False):
        if openai:
            self.send_json({"error": {"message": msg, "type": "cerberos_error"}}, status)
        else:
            self.send_json({"error": msg}, status)

    def start_stream(self, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-cache")
        self._cors()
        self.end_headers()

    def chunk(self, data):
        if isinstance(data, str):
            data = data.encode()
        if data:
            self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
            self.wfile.flush()

    def end_stream(self):
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return None

    def authorised(self):
        if not self.key or self.client_address[0] in ("127.0.0.1", "::1"):
            return True
        return self.headers.get("Authorization", "") == f"Bearer {self.key}"

    # verbs
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        self.send_json({})

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def dispatch(self, method):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        openai = path.startswith("/v1/")
        if not self.authorised():
            return self.error(401, "missing or wrong API key (CERBEROS_GATE_KEY)", openai)
        body = self.read_body() if method in ("POST", "DELETE") else {}
        if body is None:
            return self.error(400, "request body is not valid JSON", openai)
        try:
            route = ROUTES.get((method, path))
            if route:
                return route(self, body)
            if path.startswith("/api/"):
                return self.passthrough("cpu", method, path, body)
            return self.error(404, f"no such endpoint: {method} {path}", openai)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client went away; dropping the upstream response stops generation
        except urllib.error.URLError as e:
            self.error(502, f"head unreachable: {getattr(e, 'reason', e)}", openai)
        except (OSError, ValueError) as e:
            # Usually a head timing out or sending garbage mid-stream. Headers may
            # already be out, so just log it and drop the connection.
            log(f"{method} {path}: {e!r}")
            self.close_connection = True

    # --- endpoints
    def root(self, _body):
        data = b"CerberOS gate. Ollama API at /api, OpenAI API at /v1."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def version(self, _body):
        try:
            v = self.up.get_json("cpu", "/api/version").get("version", "0.0.0")
        except (urllib.error.URLError, OSError, ValueError):
            v = "0.12.0"
        self.send_json({"version": v})

    def tags(self, _body):
        self.send_json({"models": self.router.merged_tags()})

    def ps(self, _body):
        running = []
        for head in self.up.urls:
            if self.up.is_openai(head):
                continue
            try:
                running += self.up.get_json(head, "/api/ps").get("models", [])
            except (urllib.error.URLError, OSError, ValueError):
                pass
        self.send_json({"models": running})

    def v1_models(self, _body):
        data = [{"id": m["name"], "object": "model", "created": 0, "owned_by": "cerberos"}
                for m in self.router.merged_tags()]
        self.send_json({"object": "list", "data": data})

    def ollama_model_call(self, body, path):
        """/api/chat, /api/generate, /api/embed, /api/embeddings, /api/show, /api/pull, /api/delete."""
        name = body.get("model") or body.get("name") or ""
        if not name:
            return self.error(400, "model is required")
        head, upstream_name = self.router.resolve(name, for_pull=path == "/api/pull")
        if head not in self.up.urls:
            return self.error(503, f"model {name} lives on the {head} head, which isn't configured "
                                   f"(set CERBEROS_{head.upper()}_URL in /etc/cerberos/cerberos.conf)")
        fwd = dict(body)
        fwd.pop("name", None)
        fwd["model"] = upstream_name
        if self.up.is_openai(head):
            if path in ("/api/chat", "/api/generate"):
                return self.ollama_via_openai(head, fwd, name, generate=path == "/api/generate")
            if path == "/api/show":
                return self.send_json(_synth_show(name, head))
            return self.error(501, f"{path} isn't supported by an OpenAI-style remote head")
        if path == "/api/show":
            try:
                with self.up.request(head, path, fwd, timeout=10) as r:
                    return self.send_json(json.load(r))
            except (urllib.error.HTTPError, ValueError):
                return self.send_json(_synth_show(name, head))
        if path in ("/api/pull", "/api/delete") and self.router:
            self.router._tags_at = 0  # model lists are about to change
        return self.forward(head, "DELETE" if path == "/api/delete" else "POST", path, fwd,
                            rename_to=name)

    def openai_call(self, body, path):
        name = body.get("model") or ""
        if not name:
            return self.error(400, "model is required", openai=True)
        head, upstream_name = self.router.resolve(name)
        if head not in self.up.urls:
            return self.error(503, f"model {name} lives on the {head} head, which isn't configured",
                              openai=True)
        if head == "npu":
            # hailo-ollama only speaks the Ollama dialect; translate.
            if path == "/v1/chat/completions":
                return self.openai_via_ollama(head, body, upstream_name, name)
            if path == "/v1/completions":
                chat = dict(body, messages=[{"role": "user", "content": _text(body.get("prompt"))}])
                return self.openai_via_ollama(head, chat, upstream_name, name, legacy=True)
            return self.error(501, f"{path} isn't available for NPU models", openai=True)
        fwd = dict(body, model=upstream_name)
        return self.forward(head, "POST", path, fwd, rename_to=name, sse=bool(body.get("stream")))

    def passthrough(self, head, method, path, body):
        if head not in self.up.urls:
            return self.error(503, f"{head} head not configured")
        return self.forward(head, method, path, body or None)

    # --- forwarding
    def forward(self, head, method, path, body, rename_to=None, sse=False):
        try:
            resp = self.up.request(head, path, body, method=method)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(data)))
            self._cors()
            self.end_headers()
            self.wfile.write(data)
            return
        with resp:
            ctype = resp.headers.get("Content-Type", "application/json")
            self.start_stream(ctype)
            for line in resp:
                if rename_to:
                    line = _rename_line(line, rename_to, sse)
                self.chunk(line)
            self.end_stream()

    def openai_via_ollama(self, head, body, upstream_name, client_name, legacy=False):
        req = openai_to_ollama_chat(body, upstream_name)
        stream = req["stream"]
        cid = f"{'cmpl' if legacy else 'chatcmpl'}-{uuid.uuid4().hex[:24]}"
        created = int(time.time())
        obj = "text_completion" if legacy else "chat.completion"
        try:
            resp = self.up.request(head, "/api/chat", req)
        except urllib.error.HTTPError as e:
            return self.error(e.code, _upstream_error(e), openai=True)
        with resp:
            if not stream:
                text, final = "", {}
                for raw in resp:
                    if raw.strip():
                        chunk = json.loads(raw)
                        text += (chunk.get("message") or {}).get("content", "")
                        if chunk.get("done"):
                            final = chunk
                choice = ({"index": 0, "text": text, "finish_reason": "stop"} if legacy else
                          {"index": 0, "message": {"role": "assistant", "content": text},
                           "finish_reason": "stop"})
                return self.send_json({
                    "id": cid, "object": obj, "created": created, "model": client_name,
                    "choices": [choice], "usage": _usage(final)})
            self.start_stream("text/event-stream")
            first = True
            for raw in resp:
                if not raw.strip():
                    continue
                chunk = json.loads(raw)
                text = (chunk.get("message") or {}).get("content", "")
                done = chunk.get("done")
                if legacy:
                    choice = {"index": 0, "text": text, "finish_reason": "stop" if done else None}
                else:
                    delta = {"content": text}
                    if first:
                        delta["role"] = "assistant"
                    choice = {"index": 0, "delta": delta if (text or first) else {},
                              "finish_reason": "stop" if done else None}
                first = False
                event = {"id": cid, "object": obj + ("" if legacy else ".chunk"),
                         "created": created, "model": client_name, "choices": [choice]}
                if done:
                    event["usage"] = _usage(chunk)
                self.chunk(f"data: {json.dumps(event)}\n\n")
            self.chunk("data: [DONE]\n\n")
            self.end_stream()

    def ollama_via_openai(self, head, body, client_name, generate=False):
        req = ollama_to_openai_chat(body, body["model"], generate)
        stream = req["stream"]
        try:
            resp = self.up.request(head, "/v1/chat/completions", req)
        except urllib.error.HTTPError as e:
            return self.error(e.code, _upstream_error(e))

        def frame(text, done, usage=None):
            out = {"model": client_name, "created_at": _now_iso(), "done": done}
            if generate:
                out["response"] = text
            else:
                out["message"] = {"role": "assistant", "content": text}
            if done:
                out["done_reason"] = "stop"
                if usage:
                    out["prompt_eval_count"] = usage.get("prompt_tokens", 0)
                    out["eval_count"] = usage.get("completion_tokens", 0)
            return out

        with resp:
            if not stream:
                data = json.load(resp)
                text = data["choices"][0]["message"]["content"]
                return self.send_json(frame(text, True, data.get("usage")))
            self.start_stream("application/x-ndjson")
            usage = None
            for raw in resp:
                line = raw.decode(errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                event = json.loads(payload)
                usage = event.get("usage") or usage
                for ch in event.get("choices", []):
                    text = (ch.get("delta") or {}).get("content") or ""
                    if text:
                        self.chunk(json.dumps(frame(text, False)) + "\n")
            self.chunk(json.dumps(frame("", True, usage)) + "\n")
            self.end_stream()


def _upstream_error(e):
    """The error message from a head's HTTP error, whichever dialect it used."""
    raw = e.read().decode(errors="replace")
    try:
        err = json.loads(raw).get("error", raw)
        return err.get("message", raw) if isinstance(err, dict) else err
    except (ValueError, AttributeError):
        return raw or str(e)


def _usage(final):
    p, c = final.get("prompt_eval_count", 0), final.get("eval_count", 0)
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}


def _synth_show(name, head):
    return {"modelfile": "", "parameters": "", "template": "",
            "details": {"family": head, "format": "hailo" if head == "npu" else "gguf"},
            "model_info": {}, "capabilities": ["completion"]}


def _rename_line(line, name, sse):
    """Put the client-facing model name back into streamed JSON lines."""
    try:
        if sse:
            text = line.decode()
            if not text.startswith("data: {"):
                return line
            obj = json.loads(text[6:])
            obj["model"] = name
            return f"data: {json.dumps(obj)}\n".encode()
        stripped = line.strip()
        if not stripped.startswith(b"{"):
            return line
        obj = json.loads(stripped)
        if "model" in obj:
            obj["model"] = name
        return (json.dumps(obj) + "\n").encode()
    except (ValueError, UnicodeDecodeError):
        return line


ROUTES = {
    ("GET", "/"): GateHandler.root,
    ("GET", "/api/version"): GateHandler.version,
    ("GET", "/api/tags"): GateHandler.tags,
    ("GET", "/api/ps"): GateHandler.ps,
    ("GET", "/v1/models"): GateHandler.v1_models,
}
for _p in ("/api/chat", "/api/generate", "/api/embed", "/api/embeddings", "/api/show", "/api/pull"):
    ROUTES[("POST", _p)] = lambda h, b, _p=_p: h.ollama_model_call(b, _p)
ROUTES[("DELETE", "/api/delete")] = lambda h, b: h.ollama_model_call(b, "/api/delete")
for _p in ("/v1/chat/completions", "/v1/completions", "/v1/embeddings"):
    ROUTES[("POST", _p)] = lambda h, b, _p=_p: h.openai_call(b, _p)


class GateServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    verbose = False


def serve(conf=None, catalog=None, host=None, port=None, verbose=False):
    conf = conf or read_conf()
    catalog = catalog or Catalog.load()
    up = Upstream(conf)
    GateHandler.up = up
    GateHandler.router = Router(conf, catalog, up)
    GateHandler.key = conf.get("CERBEROS_GATE_KEY", "")
    host = host or ("0.0.0.0" if conf.get("CERBEROS_GATE_LAN") == "1" else "127.0.0.1")
    port = int(port or conf["CERBEROS_GATE_PORT"])
    srv = GateServer((host, port), GateHandler)
    srv.verbose = verbose
    log(f"listening on {host}:{port} -> " +
        ", ".join(f"{h}={u}" for h, u in up.urls.items()))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
