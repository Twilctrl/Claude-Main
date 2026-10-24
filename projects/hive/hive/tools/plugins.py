"""Loading tools from Python files in ``<config dir>/tools/`` (and ``$HIVE_TOOLS_PATH``).

Plugins load only from those folders, never from a project workspace: agents
can write to the workspace, so loading from there would let an agent create
its own unsandboxed tool. Plugin code runs inside Hive with your permissions.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path

from . import all_tools

log = logging.getLogger("hive.plugins")

_loaded: dict[str, list[str]] = {}
errors: dict[str, str] = {}


def plugin_dirs(config_dir: Path) -> list[Path]:
    dirs = [config_dir / "tools"]
    for p in os.environ.get("HIVE_TOOLS_PATH", "").split(os.pathsep):
        if p.strip():
            dirs.append(Path(p).expanduser())
    return dirs


def load_file(path: Path) -> list[str]:
    """Import one plugin file. Returns the tool names it registered."""
    key = str(path.resolve())
    if key in _loaded:
        return _loaded[key]
    from . import _REGISTERED

    start = len(_REGISTERED)
    name = f"hive_plugin_{path.stem}_{abs(hash(key)) % 10_000}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"can't load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    added = sorted(set(_REGISTERED[start:]))
    for n in added:
        all_tools()[n].source = str(path)
    _loaded[key] = added
    return added


def load_extra_tools(config_dir: Path) -> list[str]:
    """Register the optional built-ins (shell, web fetch) and every plugin file."""
    from . import builtin, shell, web  # noqa: F401  (registering is the import's side effect)

    names: list[str] = []
    for d in plugin_dirs(config_dir):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.py")):
            if f.name.startswith("_"):
                continue
            try:
                names += load_file(f)
                errors.pop(str(f), None)
            except Exception as e:
                errors[str(f)] = f"{type(e).__name__}: {e}"
                log.warning("tool plugin %s failed to load: %s", f, e)
    return names
