"""cerb: the CerberOS command. Run `cerb help`."""

import os
import re
import shutil
import subprocess
import sys
import time

from . import apps as A
from . import theme as t
from .client import Client, GateError, run_turn
from .config import ETC, HEAD_LABEL, Catalog, gate_url, mem_total_gb, read_conf

CONF_PATH = os.path.join(ETC, "cerberos.conf")
# Older names from earlier releases; still accepted.
ALIASES = {"summon": "chat", "devour": "pull", "banish": "rm", "heads": "status",
           "unleash": "launch", "rite": "doctor", "remove": "rm", "run": "launch",
           "list": "models", "ls": "models"}
UNITS = {"npu": "hailo-ollama", "cpu": "ollama", "gate": "cerberos-gate"}


class Ctx:
    def __init__(self):
        self.conf = read_conf()
        self.catalog = Catalog.load()
        self.client = Client(self.conf)

    def default_model(self):
        """The configured default if installed, else the first installed chatbot."""
        want = self.conf["CERBEROS_DEFAULT_MODEL"]
        names = self.client.installed_names()
        if not names or want in names:
            return want
        for m in self.catalog.models_in("chatbots"):
            if m.alias in names:
                return m.alias
        return next(iter(sorted(names)), want)


def need_root(argv):
    if os.geteuid() != 0:
        sys.stdout.flush()
        os.execvp("sudo", ["sudo", sys.executable, "-m", "cerberos"] + argv)


def from_menu_pause():
    if os.environ.get("CERBEROS_FROM_MENU") == "1" and sys.stdin.isatty():
        try:
            input(t.ash("\n  press Enter to close"))
        except (EOFError, KeyboardInterrupt):
            pass


# --- status -----------------------------------------------------------------------

def read_file(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def temp():
    raw = read_file("/sys/class/thermal/thermal_zone0/temp")
    return f"{int(raw) / 1000:.1f}°C" if raw.isdigit() else "?"


def throttle():
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True,
                             timeout=2).stdout
        val = int(out.split("=")[1], 16)
    except (OSError, IndexError, ValueError, subprocess.TimeoutExpired):
        return None, "?"
    if val == 0:
        return 0, t.ok_green("none")
    flags = []
    if val & 0x1:
        flags.append("UNDER-VOLTAGE")
    if val & 0x4:
        flags.append("throttled")
    if val & 0x8:
        flags.append("soft temp limit")
    if val & 0x50000:
        flags.append("(happened since boot)")
    return val, t.ember(f"{val:#x} " + " ".join(flags))


def meminfo():
    total = used = 0
    vals = {}
    for line in read_file("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        vals[k] = int(v.split()[0]) if v.split() else 0
    total = vals.get("MemTotal", 0)
    used = total - vals.get("MemAvailable", 0)
    return f"{used / 1048576:.1f} / {total / 1048576:.1f} GB"


def cmd_status(ctx, args):
    brief = "--brief" in args
    states = ctx.client.head_states()
    gate_up = ctx.client.alive()
    counts = {}
    if gate_up:
        try:
            for m in ctx.client.models():
                fam = (m.get("details") or {}).get("family", "")
                counts[fam] = counts.get(fam, 0) + 1
        except GateError:
            pass
    npu_dev = os.path.exists("/dev/hailo0")
    for head in ("npu", "cpu", "remote"):
        st = states[head]
        extra = {None: t.ash("not configured"), True: t.ok_green("up"),
                 False: t.red("down")}[st]
        if head == "npu" and not npu_dev:
            extra += t.ash("  (no /dev/hailo0)")
        if head == "remote" and st is not None:
            extra += t.ash("  " + ctx.conf["CERBEROS_REMOTE_URL"])
        print(f"  {t.dot(st)} {t.bone(HEAD_LABEL[head].ljust(17))} {extra}")
    lan = "LAN" if ctx.conf["CERBEROS_GATE_LAN"] == "1" else "local only"
    print(f"  {t.dot(gate_up)} {t.fire('GATE'.ljust(17))} "
          f"{gate_url(ctx.conf)} {t.ash('(' + lan + ')')}")
    print()
    t.kv("default", t.fire(ctx.default_model()) if gate_up else ctx.conf["CERBEROS_DEFAULT_MODEL"])
    t.kv("temp", temp())
    if brief:
        t.kv("ip", A.lan_ip())
        return 0
    t.kv("throttle", throttle()[1])
    t.kv("governor", read_file("/sys/devices/system/cpu/cpufreq/policy0/scaling_governor", "?"))
    t.kv("load", " ".join(read_file("/proc/loadavg", "? ? ?").split()[:3]))
    t.kv("memory", meminfo())
    du = shutil.disk_usage("/")
    t.kv("disk", f"{du.used / 1e9:.1f} / {du.total / 1e9:.1f} GB")
    t.kv("ip", A.lan_ip())
    if gate_up:
        print()
        cmd_models(ctx, ["--installed"])
    return 0


# --- models -----------------------------------------------------------------------

def cmd_models(ctx, args):
    installed = ctx.client.installed_names()
    only_installed = "--installed" in args
    origin = None
    if "--origin" in args and args.index("--origin") + 1 < len(args):
        origin = args[args.index("--origin") + 1].upper()
    elif "--us" in args:
        origin = "US"
    ram = mem_total_gb()
    for c in ctx.catalog.categories:
        ms = ctx.catalog.models_in(c.id)
        if only_installed:
            ms = [m for m in ms if m.alias in installed]
        if origin:
            ms = [m for m in ms if m.origin.upper() == origin]
        if not ms:
            continue
        print(f"  {t.blood(c.label)} {t.ash('— ' + c.tagline)}")
        for m in ms:
            have = m.alias in installed
            mark = t.ok_green("■") if have else t.ash("·")
            note = ""
            if not have and m.head == "cpu" and m.ram_gb and ram and m.ram_gb > ram:
                note = t.red(f" needs {m.ram_gb:g} GB RAM")
            elif not have and m.head == "remote" and not ctx.conf["CERBEROS_REMOTE_URL"]:
                note = t.ember(" needs the remote head")
            flag = t.bone(m.origin.ljust(3)) if m.origin == "US" else t.ash(m.origin.ljust(3))
            print(f"    {mark} {t.fire(m.alias.ljust(26))} {m.name[:32].ljust(32)} "
                  f"{flag}{t.ash(m.head.ljust(6))}{note}")
        print()
    catalog_upstreams = {m.model for m in ctx.catalog.models} | set(ctx.catalog.by_alias)
    extra = sorted(n for n in installed if n not in catalog_upstreams
                   and n.removesuffix(":latest") not in catalog_upstreams
                   and n.split("@")[0] not in catalog_upstreams)
    if extra and not origin:
        print(f"  {t.blood('Uncatalogued')} {t.ash('— pulled by hand')}")
        for n in extra:
            print(f"    {t.ok_green('■')} {n}")
        print()
    if not only_installed:
        print(t.ash("  ■ installed   · available.  cerb pull <name> to download one."))
        print(t.ash("  cerb models --us  shows only American-made models."))
    return 0


def progress_bar(frac, width=30):
    n = int(frac * width)
    return t.blood("█" * n) + t.ash("░" * (width - n))


def cmd_pull(ctx, args):
    force = "--force" in args
    args = [a for a in args if a != "--force"]
    if "--ask" in args or not args:
        print(t.ash("  Any name from ollama.com/library works, e.g. phi4-mini or granite3.3:2b."))
        print(t.ash("  Catalog names work too: cerb models"))
        try:
            name = input(t.ember("  model to download » ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
        if not name:
            return 1
        args = [name]
    rc = 0
    for name in args:
        entry = ctx.catalog.find_model(name)
        if entry:
            if entry.head == "remote" and not ctx.conf["CERBEROS_REMOTE_URL"]:
                t.fail(f"{entry.alias} runs on the remote head, and none is set. Put a bigger "
                       "machine's Ollama URL in CERBEROS_REMOTE_URL (cerb config).")
                rc = 1
                continue
            ram = mem_total_gb()
            if entry.head == "cpu" and entry.ram_gb and ram and entry.ram_gb > ram and not force:
                t.fail(f"{entry.alias} needs {entry.ram_gb:g} GB of RAM; this Pi has {ram:.0f} GB. "
                       "Use --force to try anyway.")
                rc = 1
                continue
        t.decode(f"Downloading {name}")
        last = ""
        try:
            for msg in ctx.client.pull(name):
                status = msg.get("status", "")
                total, done = msg.get("total"), msg.get("completed")
                if total and done is not None:
                    pct = done / total
                    sys.stdout.write(f"\r  {progress_bar(pct)} {pct * 100:5.1f}% "
                                     f"{t.ash(f'{done / 1e9:.2f}/{total / 1e9:.2f} GB')}  ")
                elif status != last:
                    sys.stdout.write(f"\r\033[K  {t.ash(status)}")
                last = status
                sys.stdout.flush()
            print()
            t.ok(f"{name} downloaded")
        except GateError as e:
            print()
            t.fail(f"{name}: {e}")
            rc = 1
        except KeyboardInterrupt:
            print()
            t.warn("interrupted")
            return 130
    if rc == 0 and os.geteuid() == 0:
        sync_quiet(ctx)
    return rc


def cmd_rm(ctx, args):
    if not args:
        print("usage: cerb rm <model>")
        return 2
    rc = 0
    for name in args:
        if not A.confirm(f"Delete {name}?", default=False):
            continue
        try:
            ctx.client.delete(name)
            t.ok(f"{name} deleted")
        except GateError as e:
            t.fail(f"{name}: {e}")
            rc = 1
    return rc


def set_conf(key, value):
    with open(CONF_PATH) as f:
        text = f.read()
    line = f"{key}={value}"
    if re.search(rf"^{key}=.*$", text, flags=re.M):
        text = re.sub(rf"^{key}=.*$", line, text, flags=re.M)
    else:
        text += f"\n{line}\n"
    with open(CONF_PATH, "w") as f:
        f.write(text)


def cmd_use(ctx, args):
    if not args:
        print("usage: cerb use <model>")
        return 2
    need_root(["use"] + args)
    set_conf("CERBEROS_DEFAULT_MODEL", args[0])
    t.ok(f"default model is now {t.fire(args[0])}")
    if args[0] not in ctx.client.installed_names():
        return cmd_pull(ctx, args[:1])
    return 0


# --- chat ---------------------------------------------------------------------------

CHAT_HELP = """  /model <name>   switch model         /models        list installed models
  /system <text>  set a system prompt  /clear         forget the conversation
  /save <file>    save the transcript  /exit          leave (or Ctrl-D)
  Ctrl-C          stop a reply"""


def ensure_model(ctx, name):
    """Offer to pull a model that isn't installed. Returns True when usable."""
    installed = ctx.client.installed_names()
    if name in installed or f"{name}:latest" in installed:
        return True
    print(f"{t.fire(name)} isn't installed.")
    if not A.confirm("Download it now?"):
        return False
    return cmd_pull(ctx, [name]) == 0


def cmd_chat(ctx, args):
    try:
        model = args[0] if args else ctx.default_model()
        ctx.client.get("/api/tags", timeout=3)
    except GateError as e:
        t.fail(str(e))
        return 1
    if not ensure_model(ctx, model):
        return 1
    try:
        import readline  # noqa: F401  line editing and history
    except ImportError:
        pass
    t.decode("CerberOS chat")
    print(t.ash(f"  {model} via {gate_url(ctx.conf)}   /help for commands"))
    system, messages = None, []
    you = "\001\033[1;33m\002you \001\033[1;31m\002» \001\033[0m\002" if t.TTY else "you » "
    while True:
        try:
            line = input("\n" + you).strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            continue
        if not line:
            continue
        if line.startswith("/"):
            cmd, _, arg = line.partition(" ")
            arg = arg.strip()
            if cmd in ("/exit", "/quit", "/q"):
                return 0
            if cmd == "/clear":
                messages = []
                print(t.ash("  conversation cleared"))
            elif cmd == "/model" and arg:
                if ensure_model(ctx, arg):
                    model = arg
                    print(t.ash(f"  now using {model}"))
            elif cmd == "/models":
                for n in sorted(ctx.client.installed_names()):
                    print(t.ash("  ") + n)
            elif cmd == "/system":
                system = arg or None
                print(t.ash("  system prompt " + ("set" if system else "cleared")))
            elif cmd == "/save" and arg:
                with open(os.path.expanduser(arg), "w") as f:
                    for m in messages:
                        f.write(f"## {m['role']}\n\n{m['content']}\n\n")
                print(t.ash(f"  saved {len(messages)} messages"))
            else:
                print(t.ash(CHAT_HELP))
            continue
        messages.append({"role": "user", "content": line})
        convo = ([{"role": "system", "content": system}] if system else []) + messages
        sys.stdout.write("\n" + t.blood("ai ") + t.ember("» "))
        sys.stdout.flush()
        try:
            reply, tokens, secs, ttft = run_turn(ctx.client, model, convo, _echo)
            messages.append({"role": "assistant", "content": reply})
            rate = tokens / secs if secs > 0 else 0
            print("\n" + t.ash(f"  [ {tokens} tok · {rate:.1f} tok/s · first token {ttft:.2f}s ]"))
        except KeyboardInterrupt:
            print(t.ash("\n  [stopped]"))
            messages.pop()
        except GateError as e:
            print(t.red(f"\n  {e}"))
            messages.pop()


def _echo(text):
    sys.stdout.write(t.bone(text) if t.TTY else text)
    sys.stdout.flush()


def cmd_ask(ctx, args):
    model = None
    if args[:1] in (["-m"], ["--model"]) and len(args) > 1:
        model, args = args[1], args[2:]
    prompt = " ".join(args)
    if not sys.stdin.isatty():
        piped = sys.stdin.read()
        prompt = f"{prompt}\n\n{piped}" if prompt else piped
    if not prompt.strip():
        print('usage: cerb ask [-m model] "prompt"   (stdin is appended)', file=sys.stderr)
        return 2
    try:
        run_turn(ctx.client, model or ctx.default_model(),
                 [{"role": "user", "content": prompt}], lambda s: (sys.stdout.write(s), sys.stdout.flush()))
        print()
    except GateError as e:
        t.fail(str(e))
        return 1
    except KeyboardInterrupt:
        print()
        return 130
    except BrokenPipeError:  # e.g. `cerb models | head`
        sys.stderr.close()
        return 0
    return 0


def cmd_bench(ctx, args):
    model = args[0] if args else ctx.default_model()
    prompt = "Explain in about 150 words how a neural network accelerator speeds up inference."
    t.decode(f"Benchmark: {model}")
    rates = []
    for i in range(3):
        sys.stdout.write(t.ash(f"  run {i + 1}/3 … "))
        sys.stdout.flush()
        try:
            t0 = time.monotonic()
            _, tokens, secs, ttft = run_turn(ctx.client, model,
                                             [{"role": "user", "content": prompt}], lambda s: None)
        except GateError as e:
            t.fail(str(e))
            return 1
        rate = tokens / secs if secs > 0 else 0
        rates.append(rate)
        print(t.fire(f"{rate:5.1f} tok/s") + t.ash(f"  ({tokens} tok, first token {ttft:.2f}s, "
                                                  f"{time.monotonic() - t0:.1f}s total)"))
    print(t.blood(f"  avg {sum(rates) / len(rates):.1f} tok/s"))
    return 0


# --- apps ---------------------------------------------------------------------------

def cmd_apps(ctx, args):
    want = args[0] if args else None
    for c in ctx.catalog.categories:
        if want and want not in (c.id, str(c.index), f"{c.index:02d}"):
            continue
        items = ctx.catalog.apps_in(c.id)
        if not items:
            continue
        print(f"  {t.blood(c.label)} {t.ash('— ' + c.tagline)}")
        for a in items:
            have = A.is_installed(a)
            mark = t.ok_green("■") if have else t.ash("·")
            run = t.ok_green(" running") if A.is_running(a) else ""
            print(f"    {mark} {t.fire(a.id.ljust(15))} {a.name}{run}")
            print(f"      {t.ash(a.blurb)}")
        print()
    print(t.ash("  ■ ready   · installs on first launch.   cerb launch <app>"))
    return 0


def find_app(ctx, name):
    app = ctx.catalog.apps_by_id.get(name)
    if not app:
        t.fail(f"no app called {name}. See: cerb apps")
    return app


def cmd_launch(ctx, args):
    if not args:
        print("usage: cerb launch <app or model>")
        return 2
    name, rest = args[0], args[1:]
    app = ctx.catalog.apps_by_id.get(name)
    if app:
        for needed in app.needs:
            if not ensure_model(ctx, needed):
                from_menu_pause()
                return 1
        rc = A.launch(app, lambda argv: main(argv), rest)
    elif ctx.catalog.find_model(name) or ":" in name:
        rc = cmd_chat(ctx, [name])
    else:
        t.fail(f"nothing called {name}. Try: cerb apps, cerb models")
        rc = 2
    from_menu_pause()
    return rc


def cmd_install(ctx, args):
    rc = 0
    for name in args:
        app = find_app(ctx, name)
        rc |= A.install(app) if app else 2
    return rc


def cmd_uninstall(ctx, args):
    rc = 0
    for name in args:
        app = find_app(ctx, name)
        if app and A.confirm(f"Remove {app.name}?", default=False):
            rc |= A.uninstall(app)
    return rc


def cmd_stop(ctx, args):
    rc = 0
    for name in args:
        app = find_app(ctx, name)
        rc |= A.stop(app) if app else 2
    return rc


# --- the gate -----------------------------------------------------------------------

def cmd_gate(ctx, args):
    if "--serve" in args:
        from .gate import serve
        return serve(verbose="--verbose" in args)
    port = ctx.conf["CERBEROS_GATE_PORT"]
    lan = ctx.conf["CERBEROS_GATE_LAN"] == "1"
    host = A.lan_ip() if lan else "127.0.0.1"
    base = f"http://{host}:{port}"
    up = ctx.client.alive()
    print(f"  {t.dot(up)} {t.fire('The Gate')} {t.ash('one API for every head')}")
    print()
    t.kv("Ollama API", f"{base}       (apps that 'use Ollama' find this automatically)", 11)
    t.kv("OpenAI API", f"{base}/v1    (api key: anything" +
         (", or CERBEROS_GATE_KEY from other machines)" if ctx.conf["CERBEROS_GATE_KEY"] else ")"), 11)
    t.kv("LAN access", "on" if lan else "off  (cerb config → CERBEROS_GATE_LAN=1)", 11)
    print()
    print(t.ash("  Model names: catalog paths (chatbots/gemma), raw names (gemma3:1b),"))
    print(t.ash("  or force a head with @npu / @cpu / @remote (llama3.2:1b@cpu)."))
    ext = f"http://{A.lan_ip()}:{port}"
    print()
    print(t.blood("  Connect your tools"))
    print(f"""
  {t.fire('Open WebUI / AnythingLLM')}  Ollama URL: {base}
  {t.fire('llm')}                      llm -m chatbots/gemma "hi"          (llm-ollama plugin)
  {t.fire('aider')}                    OLLAMA_API_BASE={base} aider --model ollama_chat/coding/phi
  {t.fire('OpenAI SDK')}               OpenAI(base_url="{base}/v1", api_key="cerberos")
  {t.fire('curl')}                     curl {base}/v1/chat/completions -d '{{"model":"chatbots/gemma","messages":[{{"role":"user","content":"hi"}}]}}'

  {t.fire('VS Code + Continue')}  (~/.continue/config.yaml on your laptop{'' if lan else '; needs LAN access on'})
    models:
      - name: CerberOS coder
        provider: ollama
        apiBase: {ext}
        model: coding/phi
        roles: [chat, edit]
      - name: CerberOS autocomplete (NPU, fastest)
        provider: ollama
        apiBase: {ext}
        model: coding/qwen-coder-npu
        roles: [autocomplete]

  {t.fire('Cline / Roo / any OpenAI-compatible tool')}
    Base URL {ext}/v1   API key cerberos   Model coding/phi
""")
    return 0


# --- system ---------------------------------------------------------------------------

def run_quiet(argv, timeout=10):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def cmd_doctor(ctx, args):
    t.decode("CerberOS diagnostics")
    board = read_file("/proc/device-tree/model").replace("\0", "")
    t.kv("board", board or "unknown")
    if any(s in board for s in ("Pi 5", "Compute Module 5")):
        t.ok("Pi 5 family board")
    else:
        t.warn("the AI HAT+ 2 needs a Raspberry Pi 5")
    lspci = run_quiet(["lspci"])
    if lspci and "hailo" in lspci.stdout.lower():
        t.ok("Hailo device on PCIe")
    else:
        t.fail("no Hailo device on PCIe: reseat the HAT and its ribbon cable")
    if os.path.exists("/dev/hailo0"):
        t.ok("Hailo driver loaded (/dev/hailo0)")
    else:
        t.fail("Hailo driver not loaded: sudo dkms autoinstall -k $(uname -r) && sudo reboot")
    for head, binary in (("npu", "hailo-ollama"), ("cpu", "ollama")):
        if not shutil.which(binary):
            t.fail(f"{binary} not installed ({HEAD_LABEL[head]}). See the README.")
    states = ctx.client.head_states()
    for head, st in states.items():
        if st is None and head == "remote":
            t.ok("remote head not configured (optional)")
        elif st is None:
            t.warn(f"{HEAD_LABEL[head]} not configured")
        elif st:
            t.ok(f"{HEAD_LABEL[head]} up")
        else:
            t.fail(f"{HEAD_LABEL[head]} down: cerb logs {head}")
    if ctx.client.alive():
        t.ok(f"gate open at {gate_url(ctx.conf)}")
        default = ctx.conf["CERBEROS_DEFAULT_MODEL"]
        if default in ctx.client.installed_names():
            t.ok(f"default model {default} installed")
        else:
            t.warn(f"default model {default} not installed: cerb pull {default}")
    else:
        t.fail("gate closed: cerb logs gate")
    val, text = throttle()
    if val is None:
        t.warn("can't read power/thermal state (vcgencmd missing)")
    elif val == 0:
        t.ok("no throttling or under-voltage")
    else:
        t.warn(f"throttle flags {text}: use the official 27 W PSU and an active cooler")
    if shutil.which("docker"):
        t.ok("docker present for web apps")
    if os.path.exists("/var/lib/cerberos/firstboot.done"):
        t.ok("first-boot setup complete")
    else:
        t.warn("first boot not finished: journalctl -u cerberos-firstboot")
    return 0


def cmd_logs(ctx, args):
    which = args or ["gate", "npu", "cpu"]
    units = []
    for w in which:
        units += ["-u", UNITS.get(w, w)]
    if not args:
        units += ["-u", "cerberos-firstboot"]
    os.execvp("journalctl", ["journalctl", "-f", "-n", "50"] + units)


def cmd_restart(ctx, args):
    which = args or ["npu", "cpu", "gate"]
    need_root(["restart"] + which)
    rc = subprocess.call(["systemctl", "restart"] + [UNITS.get(w, w) for w in which])
    (t.ok if rc == 0 else t.fail)("restarted " + ", ".join(which))
    return rc


def cmd_config(ctx, args):
    need_root(["config"] + args)
    editor = os.environ.get("EDITOR") or ("nano" if shutil.which("nano") else "vi")
    before = read_file(CONF_PATH)
    subprocess.call([editor, CONF_PATH])
    if read_file(CONF_PATH) != before:
        subprocess.call(["systemctl", "restart", "cerberos-gate", "cerberos-perf"])
        t.ok("settings saved; gate restarted")
    return 0


def sync_quiet(ctx):
    from .sync import sync
    try:
        sync(ctx.catalog)
    except OSError as e:
        t.warn(f"couldn't regenerate /srv/local-models and the menu: {e}")


def cmd_sync(ctx, args):
    need_root(["sync"] + args)
    from .sync import sync
    sync(ctx.catalog)
    t.ok("rebuilt /srv/local-models and the start menu from the catalog")
    return 0


def cmd_preload(ctx, args):
    """First-boot downloads: catalog models marked build/firstboot that are
    missing, then the docker apps in CERBEROS_FIRSTBOOT_APPS."""
    for _ in range(90):
        if ctx.client.alive():
            break
        time.sleep(2)
    else:
        t.fail("gate never opened")
        return 1
    time.sleep(5)  # let the heads settle
    states = ctx.client.head_states()
    installed = ctx.client.installed_names()
    npu_offers = npu_available(ctx)
    rc = 0
    for m in ctx.catalog.models:
        if m.preload not in ("build", "firstboot") or m.alias in installed:
            continue
        if not states.get(m.head):
            t.warn(f"skipping {m.alias}: {m.head} head is down")
            rc = 1
            continue
        if m.head == "npu" and npu_offers and m.model not in npu_offers:
            # e.g. Llama 3.2 1B needs Hailo GenAI 5.2+; don't retry every boot.
            t.warn(f"skipping {m.alias}: this hailo-ollama release doesn't offer {m.model}")
            continue
        rc |= cmd_pull(ctx, [m.alias])
    for app_id in ctx.conf.get("CERBEROS_FIRSTBOOT_APPS", "").split():
        app = ctx.catalog.apps_by_id.get(app_id)
        if app and not A.is_installed(app):
            rc |= A.install(app)
    return rc


def npu_available(ctx):
    """Models the installed hailo-ollama can pull, or an empty set if unknown."""
    url = ctx.conf.get("CERBEROS_NPU_URL")
    if not url:
        return set()
    try:
        data = ctx.client.get("/hailo/v1/list", base=url.rstrip("/"))
    except GateError:
        return set()
    items = data.get("models", data) if isinstance(data, dict) else data
    return {i if isinstance(i, str) else i.get("name", "") for i in items or []}


def cmd_menu(ctx, args):
    from .menu import run_menu
    return run_menu(ctx, models_only="--models" in args, cli_main=main)


def cmd_help(ctx, args):
    t.banner()
    print(f"""
  {t.blood('Just type')} {t.fire('cerb')} {t.blood('for the start menu.')} Or:

  {t.fire('cerb chat')} [model]        talk to a model
  {t.fire('cerb ask')} [-m model] "…"  one-shot answer; stdin is appended
  {t.fire('cerb models')} [--us]       every model, by strength (--us: American-made only)
  {t.fire('cerb pull')} <model>        download a model
  {t.fire('cerb rm')} <model>          delete a model
  {t.fire('cerb use')} <model>         set the default model
  {t.fire('cerb apps')} [category]     every app, by category
  {t.fire('cerb launch')} <app|model>  start anything; installs it first if needed
  {t.fire('cerb stop')} <app>          stop a web app
  {t.fire('cerb status')}              heads, gate, temperature, power
  {t.fire('cerb gate')}                API endpoints and how to connect apps
  {t.fire('cerb bench')} [model]       tokens per second
  {t.fire('cerb doctor')}              find and explain problems
  {t.fire('cerb logs')} [npu|cpu|gate] follow logs
  {t.fire('cerb restart')} [head]      restart heads and the gate
  {t.fire('cerb config')}              edit settings
  {t.fire('cerb sync')}                rebuild /srv/local-models and the menu after editing the catalog

  {t.ash('Models are named by strength: chatbots/gemma, coding/phi, … (see /srv/local-models)')}
""")
    return 0


COMMANDS = {
    "status": cmd_status, "models": cmd_models, "pull": cmd_pull, "rm": cmd_rm,
    "use": cmd_use, "chat": cmd_chat, "ask": cmd_ask, "bench": cmd_bench,
    "apps": cmd_apps, "launch": cmd_launch, "install": cmd_install,
    "uninstall": cmd_uninstall, "stop": cmd_stop, "gate": cmd_gate, "doctor": cmd_doctor,
    "logs": cmd_logs, "restart": cmd_restart, "config": cmd_config, "sync": cmd_sync,
    "preload": cmd_preload, "menu": cmd_menu, "help": cmd_help,
}


def main(argv):
    if not argv:
        argv = ["menu"] if sys.stdin.isatty() and sys.stdout.isatty() else ["help"]
    cmd, args = argv[0], argv[1:]
    if cmd in ("-h", "--help"):
        cmd = "help"
    if cmd in ("-V", "--version", "version"):
        from . import __version__
        print(f"CerberOS cerb {__version__}")
        return 0
    if cmd == "exec":
        if not args:
            t.fail("usage: cerb exec <program> [args]")
            return 2
        os.execvp(args[0], args)
    cmd = ALIASES.get(cmd, cmd)
    fn = COMMANDS.get(cmd)
    if not fn:
        t.fail(f"unknown command '{cmd}'. Try: cerb help")
        return 2
    try:
        return fn(Ctx(), args) or 0
    except KeyboardInterrupt:
        print()
        return 130
    except BrokenPipeError:  # e.g. `cerb models | head`
        sys.stderr.close()
        return 0
