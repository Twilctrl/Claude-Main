"""Talking to the gate (and, for status, to each head directly)."""

import json
import time
import urllib.error
import urllib.request

from .config import gate_url, head_urls


class GateError(Exception):
    pass


class Client:
    def __init__(self, conf):
        self.conf = conf
        self.url = gate_url(conf)

    def _req(self, path, body=None, method=None, timeout=600, base=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request((base or self.url) + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error", "")
                msg = msg.get("message", msg) if isinstance(msg, dict) else msg
            except ValueError:
                msg = ""
            raise GateError(f"{e.code} {msg or e.reason}") from None
        except (urllib.error.URLError, OSError) as e:
            raise GateError(f"the gate isn't answering at {self.url} ({getattr(e, 'reason', e)}). "
                            "Try: cerb doctor") from None

    def get(self, path, timeout=5, base=None):
        with self._req(path, timeout=timeout, base=base) as r:
            return json.load(r)

    def alive(self, base=None, path="/api/tags", timeout=1.5):
        try:
            self.get(path, timeout=timeout, base=base)
            return True
        except GateError:
            return False

    def head_states(self):
        """{head: True/False/None (not configured)}"""
        urls = head_urls(self.conf)
        out = {}
        for head in ("npu", "cpu", "remote"):
            if head not in urls:
                out[head] = None
            elif head == "remote" and self.conf.get("CERBEROS_REMOTE_API") == "openai":
                out[head] = self.alive(urls[head], "/v1/models")
            else:
                out[head] = self.alive(urls[head])
        return out

    def models(self):
        return self.get("/api/tags").get("models", [])

    def installed_names(self):
        try:
            return {m["name"] for m in self.models()}
        except GateError:
            return set()

    def stream_chat(self, model, messages):
        """Yield (text, final_chunk_or_None)."""
        with self._req("/api/chat", {"model": model, "messages": messages, "stream": True}) as r:
            for raw in r:
                raw = raw.strip()
                if not raw:
                    continue
                chunk = json.loads(raw)
                if "error" in chunk:
                    raise GateError(chunk["error"])
                text = (chunk.get("message") or {}).get("content", "")
                yield text, (chunk if chunk.get("done") else None)

    def pull(self, model):
        """Yield progress dicts."""
        with self._req("/api/pull", {"model": model, "stream": True}, timeout=3600) as r:
            for raw in r:
                if raw.strip():
                    msg = json.loads(raw)
                    if "error" in msg:
                        raise GateError(msg["error"])
                    yield msg

    def delete(self, model):
        with self._req("/api/delete", {"model": model}, method="DELETE"):
            pass


def run_turn(client, model, messages, write, stats=True):
    """Stream one reply through `write`. Returns (text, tokens, gen_seconds, ttft)."""
    start = time.monotonic()
    first = None
    pieces, chunks, final = [], 0, None
    for text, done in client.stream_chat(model, messages):
        if text:
            if first is None:
                first = time.monotonic()
            pieces.append(text)
            chunks += 1
            write(text)
        if done:
            final = done
    end = time.monotonic()
    tokens = (final or {}).get("eval_count") or chunks
    gen_ns = (final or {}).get("eval_duration")
    # Without server timings, time the whole reply: the first-token gap is part of it.
    secs = gen_ns / 1e9 if gen_ns else end - start
    return "".join(pieces), tokens, secs, (first or end) - start
