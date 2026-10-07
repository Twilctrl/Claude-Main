"""Install, launch and stop the apps in the catalog."""

import os
import shutil
import socket
import subprocess
import sys
import time

from .config import LIB
from . import theme as t

PIPX_ENV = {"PIPX_HOME": "/opt/pipx", "PIPX_BIN_DIR": "/usr/local/bin",
            "PIPX_MAN_DIR": "/usr/local/share/man"}


def app_dir(app):
    return os.path.join(LIB, "apps", app.id)


def sudo(cmd, env=None):
    """Run a command as root, via sudo when we aren't root already."""
    full = list(cmd)
    if env:
        full = ["env"] + [f"{k}={v}" for k, v in env.items()] + full
    if os.geteuid() != 0:
        full = ["sudo"] + full
    return subprocess.call(full)


def docker_cmd():
    if not shutil.which("docker"):
        return None
    base = ["docker"]
    if os.geteuid() != 0 and subprocess.call(["docker", "info"], stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL) != 0:
        base = ["sudo", "docker"]
    return base


def compose(app, *args):
    d = docker_cmd()
    if not d:
        t.fail("Docker isn't installed: sudo apt install docker.io docker-compose")
        return 1
    # Prefer the `docker compose` plugin; fall back to the standalone binary.
    if subprocess.call(d + ["compose", "version"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL) == 0:
        base = d + ["compose"]
    elif shutil.which("docker-compose"):
        base = d[:-1] + ["docker-compose"]
    else:
        t.fail("Docker Compose isn't installed: sudo apt install docker-compose")
        return 1
    return subprocess.call(base + ["-p", f"cerberos-{app.id}",
                                   "-f", os.path.join(app_dir(app), "compose.yml"), *args])


def is_installed(app):
    if app.kind == "builtin":
        return True
    if app.kind == "pipx" or app.kind == "script":
        return bool(app.bin and (os.path.exists(app.bin) or shutil.which(app.bin)))
    if app.kind == "docker":
        d = docker_cmd()
        return bool(d) and subprocess.call(d + ["image", "inspect", app.image],
                                           stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL) == 0
    return False


def is_running(app):
    if app.kind != "docker" or not app.port:
        return False
    return port_open(app.port)


def port_open(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def install(app):
    t.decode(f"Installing {app.name}")
    if app.kind == "builtin":
        t.ok("built in; nothing to install")
        return 0
    if app.kind == "pipx":
        rc = sudo(["pipx", "install", "--force", app.package], env=PIPX_ENV)
        for plugin in app.plugins:
            if rc == 0:
                rc = sudo(["pipx", "inject", app.package, plugin], env=PIPX_ENV)
    elif app.kind == "docker":
        rc = compose(app, "pull")
    elif app.kind == "script":
        rc = sudo([os.path.join(app_dir(app), "install.sh")])
    else:
        t.fail(f"unknown app kind {app.kind}")
        return 1
    (t.ok if rc == 0 else t.fail)(f"{app.name} {'installed' if rc == 0 else 'failed to install'}")
    return rc


def uninstall(app):
    if app.kind == "builtin":
        t.warn("built-in apps can't be removed")
        return 1
    if app.kind == "pipx":
        return sudo(["pipx", "uninstall", app.package], env=PIPX_ENV)
    if app.kind == "docker":
        rc = compose(app, "down")
        d = docker_cmd()
        return rc or subprocess.call(d + ["image", "rm", app.image])
    if app.kind == "script":
        script = os.path.join(app_dir(app), "uninstall.sh")
        if os.path.exists(script):
            return sudo([script])
    t.warn("no uninstaller for this app")
    return 1


def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def launch(app, cli_main, args=()):
    """Start an app. Offers to install it first."""
    if not is_installed(app):
        print(f"{t.fire(app.name)} isn't installed yet. {t.ash(app.blurb)}")
        if not confirm("Install it now?"):
            return 1
        if install(app) != 0:
            return 1
    env = dict(os.environ, **app.env)
    if app.kind == "builtin":
        if app.run[:1] == ["exec"]:
            return exec_or_fail(app.run[1:] + list(args), env)
        return cli_main(app.run + list(args))
    if app.kind == "docker":
        return launch_docker(app)
    return exec_or_fail(app.run + list(args), env)


def exec_or_fail(argv, env):
    try:
        sys.stdout.flush()
        os.execvpe(argv[0], argv, env)
    except OSError as e:
        t.fail(f"couldn't start {argv[0]}: {e}")
        return 1


def launch_docker(app):
    if not is_running(app):
        print(t.ash(f"  starting {app.name}..."))
        if compose(app, "up", "-d") != 0:
            return 1
        for i in range(180):
            if port_open(app.port):
                break
            sys.stdout.write("\r  " + t.ember("starting " + "▓" * (i % 20) + "░" * (19 - i % 20)))
            sys.stdout.flush()
            time.sleep(1)
        print()
    url_local = f"http://127.0.0.1:{app.port}"
    url_lan = f"http://{lan_ip()}:{app.port}"
    if not port_open(app.port):
        t.warn(f"{app.name} is still starting. It will be at {url_lan} shortly. "
               f"Logs: docker logs -f cerberos-{app.id}")
        return 1
    t.ok(f"{app.name} is up")
    t.kv("here", url_local)
    t.kv("on LAN", url_lan)
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        subprocess.Popen(["xdg-open", url_local], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    return 0


def stop(app):
    if app.kind != "docker":
        t.warn("only web apps keep running in the background")
        return 1
    return compose(app, "down")


def confirm(question, default=True):
    if not sys.stdin.isatty():
        return default
    hint = "[Y/n]" if default else "[y/N]"
    try:
        ans = input(f"{t.ember('?')} {question} {t.ash(hint)} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return default if not ans else ans.startswith("y")

