#!/usr/bin/env python3
"""Stream a local LLM's output to the Feather RP2350, which types it on a laptop.

Talks to an Ollama-compatible server (default: hailo-ollama on
http://localhost:8000) and to the Feather over the Pi's UART (/dev/serial0).
Requires: pyserial (sudo apt install python3-serial)

    python3 llm_bridge.py                      # interactive: type prompts here
    python3 llm_bridge.py "write a haiku"      # one-shot
    python3 llm_bridge.py --headless           # run from boot with no display

Headless mode (used by the systemd service) waits for the LLM server and the
Feather to come up, retries forever instead of exiting, and shows its state on
the Pi's green activity LED:

    heartbeat blink  starting up / waiting for the LLM server or the Feather
    solid on         ready: type a prompt on the Pi's keyboard, press Enter
    fast blink       generating and typing a reply

While a reply is being typed, Ctrl-C on the Pi's keyboard (or the Feather's arm
button) stops it. Lines starting with "/" are commands, not prompts:
    /speed <cps>   set typing speed        /enter   toggle Enter after replies
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

import serial

LED_DIRS = ("/sys/class/leds/ACT", "/sys/class/leds/led0")


def log(msg):
    print("[llm-keyboard] " + msg, file=sys.stderr, flush=True)


class FeatherError(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


class StatusLed:
    """Drives the Pi's green ACT LED so you can see state without a display."""

    def __init__(self, enabled):
        self.path = None
        self.state = None
        self.original = None
        if not enabled:
            return
        for d in LED_DIRS:
            if os.access(os.path.join(d, "trigger"), os.W_OK):
                self.path = d
                break
        if self.path:
            try:
                with open(os.path.join(self.path, "trigger")) as f:
                    current = [t for t in f.read().split() if t.startswith("[")]
                self.original = current[0].strip("[]") if current else None
            except OSError:
                pass

    def _write(self, name, value):
        try:
            with open(os.path.join(self.path, name), "w") as f:
                f.write(value)
        except OSError:
            pass

    def set(self, state):
        if not self.path or state == self.state:
            return
        self.state = state
        if state == "waiting":
            self._write("trigger", "heartbeat")
        elif state == "ready":
            self._write("trigger", "none")
            self._write("brightness", "1")
        elif state == "busy":
            self._write("trigger", "timer")
            self._write("delay_on", "80")
            self._write("delay_off", "80")

    def restore(self):
        if self.path and self.original:
            self._write("trigger", self.original)


class Feather:
    def __init__(self, port, baud):
        self.port = port
        self.baud = baud
        self.ser = None
        self.cps = 40

    def open(self):
        self.close()
        self.ser = serial.Serial(self.port, self.baud, timeout=0.1)
        self.ser.reset_input_buffer()
        self.command("PING")
        return self.status()

    def close(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except serial.SerialException:
                pass
            self.ser = None

    def _readline(self, deadline):
        buf = b""
        while time.monotonic() < deadline:
            buf += self.ser.readline()
            if buf.endswith(b"\n"):
                return buf.decode("utf-8", "replace").strip()
        raise FeatherError("timed out waiting for the Feather")

    def command(self, line, timeout=5.0):
        """Send one command and wait for its OK/ERR reply."""
        try:
            self.ser.write((line + "\n").encode("utf-8"))
            deadline = time.monotonic() + timeout
            while True:
                resp = self._readline(deadline)
                if resp.startswith("EVT"):
                    log("feather: " + resp)
                    continue
                if resp.startswith("OK"):
                    return resp
                raise FeatherError(resp)
        except (serial.SerialException, OSError) as e:
            raise FeatherError("serial error: %s" % e)

    def status(self):
        resp = self.command("STATUS")
        fields = dict(f.partition("=")[::2] for f in resp.split()[1:])
        self.cps = int(fields.get("cps", self.cps))
        return fields.get("armed") == "1", resp

    def wait_armed(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            armed, _ = self.status()
            if armed:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.5)

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


# ---------------------------------------------------------------------------
# LLM server (Ollama API: hailo-ollama, Ollama, ...)
# ---------------------------------------------------------------------------
def api(base_url, path, body=None, timeout=10):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base_url.rstrip("/") + path, data=data,
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def wait_for_llm(args, led):
    """Block until the LLM server answers, then warn if the model is missing."""
    announced = False
    while True:
        try:
            with api(args.llm_url, "/api/tags") as resp:
                tags = json.load(resp)
            break
        except urllib.error.HTTPError:
            tags = None          # server is up; it just lacks this endpoint
            break
        except (urllib.error.URLError, OSError, ValueError) as e:
            if not announced:
                log("waiting for LLM server at %s (%s)" % (args.llm_url, e))
                announced = True
            led.set("waiting")
            time.sleep(3)
    log("LLM server is up at " + args.llm_url)
    if tags:
        names = [m.get("name") or m.get("model") for m in tags.get("models", [])]
        if names and args.model not in names and args.model + ":latest" not in names:
            log("warning: model %r not found on server; available: %s"
                % (args.model, ", ".join(n for n in names if n)))


def warm_up(args):
    """Load the model now so the first real prompt doesn't pay the load time."""
    log("loading model %s ..." % args.model)
    start = time.monotonic()
    try:
        for _ in stream_reply(args, "Say OK.", max_tokens=1):
            pass
        log("model ready (%.1fs)" % (time.monotonic() - start))
    except (LLMError, urllib.error.URLError, OSError, ValueError) as e:
        log("warm-up failed (will still try prompts): %s" % e)


def stream_reply(args, prompt, max_tokens=None):
    if args.api == "chat":
        messages = []
        if args.system:
            messages.append({"role": "system", "content": args.system})
        messages.append({"role": "user", "content": prompt})
        path, body = "/api/chat", {"model": args.model, "messages": messages}
    else:
        path, body = "/api/generate", {"model": args.model, "prompt": prompt}
        if args.system:
            body["system"] = args.system
    body["stream"] = True
    if max_tokens:
        body["options"] = {"num_predict": max_tokens}

    with api(args.llm_url, path, body, timeout=args.llm_timeout) as resp:
        for raw in resp:
            if not raw.strip():
                continue
            chunk = json.loads(raw)
            if chunk.get("error"):
                raise LLMError(chunk["error"])
            text = (chunk.get("message") or {}).get("content") or chunk.get("response")
            if text:
                yield text
            if chunk.get("done"):
                return


# ---------------------------------------------------------------------------
# Prompt handling
# ---------------------------------------------------------------------------
def connect_feather(args, led):
    feather = Feather(args.port, args.baud)
    announced = False
    while True:
        try:
            _, status = feather.open()
            log("feather connected: " + status)
            if args.cps:
                feather.set_speed(args.cps)
            return feather
        except (FeatherError, serial.SerialException, OSError) as e:
            feather.close()
            if not args.headless:
                raise SystemExit("can't reach the Feather on %s: %s" % (args.port, e))
            if not announced:
                log("waiting for the Feather on %s (%s)" % (args.port, e))
                announced = True
            led.set("waiting")
            time.sleep(3)


def run_prompt(feather, args, prompt):
    """Generate a reply and type it, flushing in small batches as tokens arrive.

    Raises FeatherError if the link to the Feather is broken (caller reconnects).
    """
    if not feather.wait_armed(args.arm_wait):
        log("feather is disarmed -- press its arm button, then re-enter the prompt")
        return
    pending = ""
    last_flush = time.monotonic()
    try:
        for token in stream_reply(args, prompt):
            if args.echo:
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
    except KeyboardInterrupt:
        log("stopped")
        feather.command("RELEASE")
    except FeatherError as e:
        if str(e) != "ERR DISARMED":
            raise
        log("feather disarmed -- output stopped")
    except (LLMError, urllib.error.URLError, OSError, ValueError) as e:
        log("LLM error: %s" % e)
    finally:
        if args.echo:
            print()


def handle_command(line, feather, args):
    cmd, _, arg = line[1:].partition(" ")
    if cmd == "speed" and arg.strip().isdigit():
        feather.set_speed(int(arg))
        log("typing speed %s cps" % arg.strip())
    elif cmd == "enter":
        args.enter = not args.enter
        log("press Enter after replies: %s" % ("on" if args.enter else "off"))
    else:
        log("unknown command: " + line)


def interactive(feather, args, led):
    if not args.headless:
        print("Enter a prompt (Ctrl-D to quit).", file=sys.stderr)
    while True:
        led.set("ready")
        try:
            line = input("" if args.headless else "> ").strip()
        except KeyboardInterrupt:
            if args.headless:
                continue
            print()
            return
        except EOFError:
            if not args.headless:
                print()
                return
            time.sleep(1)       # Ctrl-D on the console, or no stdin at all
            continue
        if not line:
            continue
        led.set("busy")
        try:
            if line.startswith("/"):
                handle_command(line, feather, args)
            else:
                run_prompt(feather, args, line)
        except FeatherError as e:
            if not args.headless:
                raise
            log("lost the Feather (%s); reconnecting" % e)
            feather.close()
            feather = connect_feather(args, led)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("prompt", nargs="*", help="prompt (omit for interactive mode)")
    p.add_argument("--port", default="/dev/serial0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--llm-url", "--ollama", default="http://localhost:8000",
                   help="Ollama-compatible server (hailo-ollama: port 8000, "
                        "stock Ollama: http://localhost:11434)")
    p.add_argument("--model", default="llama3.2:3b")
    p.add_argument("--api", choices=("chat", "generate"), default="chat",
                   help="which Ollama endpoint to use")
    p.add_argument("--system", default=("Reply in plain ASCII text only. "
                                        "No markdown formatting."))
    p.add_argument("--cps", type=int, help="typing speed, characters per second")
    p.add_argument("--batch", type=int, default=48,
                   help="max characters per TYPE command")
    p.add_argument("--enter", action="store_true",
                   help="press Enter after each reply")
    p.add_argument("--arm-wait", type=float, default=30,
                   help="seconds to wait for the arm button after a prompt")
    p.add_argument("--llm-timeout", type=float, default=300)
    p.add_argument("--headless", action="store_true",
                   help="run unattended from boot: wait for everything, never "
                        "exit on errors, show status on the ACT LED")
    p.add_argument("--no-warmup", action="store_true",
                   help="don't pre-load the model at startup")
    args = p.parse_args()
    args.echo = not args.headless

    led = StatusLed(args.headless)
    led.set("waiting")
    try:
        wait_for_llm(args, led)
        if not args.no_warmup and not args.prompt:
            warm_up(args)
        feather = connect_feather(args, led)
        if args.prompt:
            run_prompt(feather, args, " ".join(args.prompt))
        else:
            interactive(feather, args, led)
    finally:
        led.restore()


if __name__ == "__main__":
    main()
