#!/usr/bin/env python3
"""Stream a local LLM's output to the Feather RP2350, which types it on a laptop.

Talks to an Ollama server (default http://localhost:11434) and to the Feather
over the Pi's UART (/dev/serial0). Requires: pip install pyserial

    python3 llm_bridge.py                      # interactive: type prompts here
    python3 llm_bridge.py "write a haiku"      # one-shot
    python3 llm_bridge.py --model llama3.2:3b --cps 60 "..."
"""

import argparse
import json
import sys
import time
import urllib.request

import serial


class FeatherError(RuntimeError):
    pass


class Feather:
    def __init__(self, port, baud):
        self.ser = serial.Serial(port, baud, timeout=0.1)
        self.cps = 40

    def _readline(self, deadline):
        buf = b""
        while time.monotonic() < deadline:
            buf += self.ser.readline()
            if buf.endswith(b"\n"):
                return buf.decode("utf-8", "replace").strip()
        raise FeatherError("timed out waiting for the Feather")

    def command(self, line, timeout=5.0):
        """Send one command and wait for its OK/ERR reply."""
        self.ser.write((line + "\n").encode("utf-8"))
        deadline = time.monotonic() + timeout
        while True:
            resp = self._readline(deadline)
            if resp.startswith("EVT"):
                print("[feather] " + resp, file=sys.stderr)
                continue
            if resp.startswith("OK"):
                return resp
            raise FeatherError(resp)

    def connect(self):
        self.ser.reset_input_buffer()
        self.command("PING")
        status = self.command("STATUS")
        for field in status.split()[1:]:
            key, _, value = field.partition("=")
            if key == "cps":
                self.cps = int(value)
        return status

    def set_speed(self, cps):
        self.command("SPEED %d" % cps)
        self.cps = cps

    def type(self, text):
        if not text:
            return
        escaped = (text.replace("\\", "\\\\").replace("\r", "")
                   .replace("\n", "\\n").replace("\t", "\\t"))
        self.command("TYPE " + escaped, timeout=5.0 + 2.0 * len(text) / self.cps)

    def key(self, combos):
        self.command("KEY " + combos)


def stream_ollama(url, model, prompt, system=None):
    body = {"model": model, "prompt": prompt, "stream": True}
    if system:
        body["system"] = system
    req = urllib.request.Request(
        url.rstrip("/") + "/api/generate",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        for raw in resp:
            if not raw.strip():
                continue
            chunk = json.loads(raw)
            if chunk.get("response"):
                yield chunk["response"]
            if chunk.get("done"):
                return


def run_prompt(feather, args, prompt):
    """Generate a reply and type it, flushing in small batches as tokens arrive."""
    pending = ""
    last_flush = time.monotonic()
    try:
        for token in stream_ollama(args.ollama, args.model, prompt, args.system):
            sys.stdout.write(token)
            sys.stdout.flush()
            pending += token
            if ("\n" in pending or len(pending) >= args.batch
                    or time.monotonic() - last_flush > 0.3):
                feather.type(pending)
                pending = ""
                last_flush = time.monotonic()
        feather.type(pending)
        if args.enter:
            feather.key("ENTER")
    except FeatherError as e:
        if str(e) == "ERR DISARMED":
            print("\n[feather] disarmed -- press the arm button on the Feather "
                  "(output stopped)", file=sys.stderr)
        else:
            print("\n[feather] " + str(e), file=sys.stderr)
    print()


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("prompt", nargs="*", help="prompt (omit for interactive mode)")
    p.add_argument("--port", default="/dev/serial0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--ollama", default="http://localhost:11434")
    p.add_argument("--model", default="llama3.2:3b")
    p.add_argument("--system", default=("Reply in plain ASCII text only. "
                                        "No markdown formatting."))
    p.add_argument("--cps", type=int, help="typing speed, characters per second")
    p.add_argument("--batch", type=int, default=48,
                   help="max characters per TYPE command")
    p.add_argument("--enter", action="store_true",
                   help="press Enter after each reply")
    args = p.parse_args()

    feather = Feather(args.port, args.baud)
    print("[feather] " + feather.connect(), file=sys.stderr)
    if args.cps:
        feather.set_speed(args.cps)

    if args.prompt:
        run_prompt(feather, args, " ".join(args.prompt))
        return

    print("Enter a prompt (Ctrl-D to quit).", file=sys.stderr)
    while True:
        try:
            prompt = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if prompt:
            run_prompt(feather, args, prompt)


if __name__ == "__main__":
    main()
