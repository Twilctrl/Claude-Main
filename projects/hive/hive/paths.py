"""Where Hive keeps its config and data, per platform. Nothing is hardcoded.

Config: ``$HIVE_CONFIG_DIR``, else ``$XDG_CONFIG_HOME/hive`` (Linux),
``%APPDATA%\\hive`` (Windows) or ``~/Library/Application Support/hive`` (macOS).
Data: ``$HIVE_DATA_DIR``, else ``$XDG_DATA_HOME/hive``, ``%LOCALAPPDATA%\\hive``
or the same macOS folder.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def config_dir() -> Path:
    """Directory holding backends.yaml, hive.yaml, roles/ and tools/."""
    if env := os.environ.get("HIVE_CONFIG_DIR"):
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(_home() / "AppData" / "Roaming")
        return Path(base) / "hive"
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support" / "hive"
    base = os.environ.get("XDG_CONFIG_HOME") or str(_home() / ".config")
    return Path(base) / "hive"


def data_dir() -> Path:
    """Directory holding hive.db and the default project workspaces."""
    if env := os.environ.get("HIVE_DATA_DIR"):
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(_home() / "AppData" / "Local")
        return Path(base) / "hive"
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support" / "hive"
    base = os.environ.get("XDG_DATA_HOME") or str(_home() / ".local" / "share")
    return Path(base) / "hive"


def db_path() -> Path:
    return data_dir() / "hive.db"


def workspaces_dir() -> Path:
    return data_dir() / "workspaces"
