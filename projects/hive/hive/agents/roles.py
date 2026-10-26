"""Role templates: the built-ins in ``hive/roles/``, overridable from ``<config>/roles/``."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .. import paths
from ..config import ConfigError

BUILTIN_DIR = Path(__file__).resolve().parent.parent / "roles"


class Role(BaseModel):
    """A reusable agent template. See DESIGN.md §7."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    system_prompt: str
    tools: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    context_budget: int | None = None
    wake_on_broadcast: bool = False
    approval: Literal["ask", "allow", "deny"] = "ask"
    memory: bool | str = True  # False: no long-term memory; a string: share that namespace


def _load_file(path: Path) -> Role:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data.setdefault("name", path.stem)
        return Role.model_validate(data)
    except Exception as e:
        raise ConfigError(f"role file {path}: {e}") from None


def list_roles(config_dir: Path | None = None) -> dict[str, Role]:
    """Every role by name. A user file with a built-in's name replaces the built-in."""
    roles = {p.stem: _load_file(p) for p in sorted(BUILTIN_DIR.glob("*.yaml"))}
    user = (config_dir or paths.config_dir()) / "roles"
    if user.is_dir():
        for p in sorted(user.glob("*.yaml")):
            roles[p.stem] = _load_file(p)
    return roles


def load_role(name: str, config_dir: Path | None = None) -> Role:
    roles = list_roles(config_dir)
    try:
        return roles[name]
    except KeyError:
        raise ConfigError(f"no role named {name!r} (have: {', '.join(sorted(roles))})") from None


def builtin_role_path(name: str) -> Path:
    return BUILTIN_DIR / f"{name}.yaml"
