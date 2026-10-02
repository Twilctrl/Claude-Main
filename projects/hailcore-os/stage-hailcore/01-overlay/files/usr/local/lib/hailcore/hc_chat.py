#!/usr/bin/env python3
"""hailcore chat client: talks to hailo-ollama (or any Ollama-compatible API).

    hc chat [model]          interactive chat
    hc ask "prompt"          one-shot; stdin is appended if piped
    hc bench [model]         tokens-per-second benchmark

Standard library only, so it runs on a bare image.
"""

import json
import os
import random
import sys
import time
import urllib.error
import urllib.request

CONF = "/etc/hailcore/hailcore.conf"
TTY = sys.stdout.isatty()
FX = TTY and os.environ.get("HAILCORE_NO_FX") != "1"


def color(code):
    return (lambda s: f"\033[{code}m{s}\033[0m") if TTY else (lambda s: s)


cyan, bcyan, mag, green, yellow, red, dim = (
    color(c) for c in ("36", "1;96", "95", "92", "93", "91", "2;35"))


def load_conf():
    conf = {}
    try:
        with open(CONF) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    conf[k.strip()] = v.strip().strip("'\"")
    except OSError:
        pass
    return conf


CFG = load_conf()
URL = os.environ.get("HAILCORE_LLM_URL", CFG.get("HAILCORE_LLM_URL", "http://127.0.0.1:8000")).rstrip("/")
MODEL = os.environ.get("HAILCORE_MODEL", CFG.get("HAILCORE_MODEL", "qwen2.5-instruct:1.5b"))


def decode(text, duration=0.5):
    """Print text with a short 'decrypting' glitch effect."""
    if not FX:
        print(bcyan(text))
        return
    glyphs = "!<>-_\\/[]{}=+*^?#01"
    steps = 12
    for i in range(steps + 1):
        fixed = int(len(text) * i / steps)
        noise = "".join(c if c == " " else random.choice(glyphs) for c in text[fixed:])
        sys.stdout.write("\r" + bcyan(text[:fixed]) + mag(noise))
        sys.stdout.flush()
        time.sleep(duration / steps)
    sys.stdout.write("\n")


def stream(model, messages):
    """Yield (text, final_chunk_or_None) from /api/chat."""
    body = json.dumps({"model": model, "messages": messages, "stream": True}).encode()
    req = urllib.request.Request(URL + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        for raw in resp:
            raw = raw.strip()
            if not raw:
                continue
            chunk = json.loads(raw)
            if "error" in chunk:
                raise RuntimeError(chunk["error"])
            text = (chunk.get("message") or {}).get("content", "")
            yield text, (chunk if chunk.get("done") else None)


def run_turn(model, messages, out=sys.stdout, show_stats=True):
    """Stream one reply to `out`. Returns (reply_text, tokens, seconds)."""
    start = time.monotonic()
    first = None
    pieces, chunks, final = [], 0, None
    for text, done in stream(model, messages):
        if text:
            if first is None:
                first = time.monotonic()
            pieces.append(text)
            chunks += 1
            out.write(cyan(text) if out is sys.stdout else text)
            out.flush()
        if done:
            final = done
    end = time.monotonic()
    # Prefer the server's own counters; fall back to counting streamed chunks.
    tokens = (final or {}).get("eval_count") or chunks
    gen_ns = (final or {}).get("eval_duration")
    secs = gen_ns / 1e9 if gen_ns else end - (first or start)
    if show_stats and TTY:
        ttft = (first or end) - start
        rate = tokens / secs if secs > 0 else 0
        print("\n" + dim(f"  ⟦ {tokens} tok · {rate:.1f} tok/s · first token {ttft:.2f}s ⟧"))
    elif out is sys.stdout:
        print()
    return "".join(pieces), tokens, secs


def check_server():
    try:
        with urllib.request.urlopen(URL + "/api/tags", timeout=3):
            return True
    except (urllib.error.URLError, OSError):
        print(red(f"LLM server not reachable at {URL}.") + " Try: hc doctor", file=sys.stderr)
        return False


HELP = """  /model <name>   switch model        /system <text>  set a system prompt
  /clear          forget the chat     /save <file>    save the transcript
  /exit           quit (or Ctrl-D)    Ctrl-C          stop a reply"""


def cmd_chat(args):
    model = args[0] if args else MODEL
    if not check_server():
        return 1
    try:
        import readline  # noqa: F401  (line editing + history for input())
    except ImportError:
        pass
    decode("// HAILCORE :: NEURAL LINK ESTABLISHED")
    print(dim(f"  model {model} @ {URL}   /help for commands"))
    system, messages = None, []
    while True:
        try:
            line = input("\n" + ("\001\033[1;95m\002you \001\033[1;93m\002» \001\033[0m\002" if TTY else "you » "))
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            continue
        line = line.strip()
        if not line:
            continue
        if line.startswith("/"):
            cmd, _, arg = line.partition(" ")
            if cmd in ("/exit", "/quit"):
                return 0
            elif cmd == "/help":
                print(dim(HELP))
            elif cmd == "/clear":
                messages = []
                print(dim("  memory wiped"))
            elif cmd == "/model" and arg:
                model = arg.strip()
                print(dim(f"  model → {model}"))
            elif cmd == "/system":
                system = arg.strip() or None
                print(dim("  system prompt " + ("set" if system else "cleared")))
            elif cmd == "/save" and arg:
                with open(os.path.expanduser(arg.strip()), "w") as f:
                    for m in messages:
                        f.write(f"## {m['role']}\n\n{m['content']}\n\n")
                print(dim(f"  saved {len(messages)} messages"))
            else:
                print(dim(HELP))
            continue
        messages.append({"role": "user", "content": line})
        convo = ([{"role": "system", "content": system}] if system else []) + messages
        sys.stdout.write(bcyan("\nai ") + yellow("» "))
        try:
            reply, _, _ = run_turn(model, convo)
            messages.append({"role": "assistant", "content": reply})
        except KeyboardInterrupt:
            print(dim("\n  [interrupted]"))
            messages.pop()
        except (urllib.error.URLError, RuntimeError, OSError) as e:
            print(red(f"\n  error: {e}"))
            messages.pop()


def cmd_ask(args):
    prompt = " ".join(args)
    if not sys.stdin.isatty():
        piped = sys.stdin.read()
        prompt = f"{prompt}\n\n{piped}" if prompt else piped
    if not prompt.strip():
        print('usage: hc ask "prompt"', file=sys.stderr)
        return 2
    if not check_server():
        return 1
    try:
        run_turn(MODEL, [{"role": "user", "content": prompt}], show_stats=False)
    except KeyboardInterrupt:
        print()
        return 130
    return 0


def cmd_bench(args):
    model = args[0] if args else MODEL
    if not check_server():
        return 1
    prompt = "Explain in about 150 words how a neural network accelerator speeds up inference."
    decode(f"// BENCH {model}")
    results = []
    with open(os.devnull, "w") as null:
        for i in range(3):
            sys.stdout.write(dim(f"  run {i + 1}/3 … "))
            sys.stdout.flush()
            t0 = time.monotonic()
            _, tokens, secs = run_turn(model, [{"role": "user", "content": prompt}], out=null, show_stats=False)
            wall = time.monotonic() - t0
            rate = tokens / secs if secs > 0 else 0
            results.append(rate)
            print(green(f"{rate:5.1f} tok/s") + dim(f"  ({tokens} tok, {wall:.1f}s wall)"))
    print(bcyan(f"  avg {sum(results) / len(results):.1f} tok/s"))
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd, args = sys.argv[1], sys.argv[2:]
    fn = {"chat": cmd_chat, "ask": cmd_ask, "bench": cmd_bench}.get(cmd)
    if not fn:
        print(__doc__)
        return 2
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
