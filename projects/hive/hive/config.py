"""Configuration: ``backends.yaml`` (model servers) and ``hive.yaml`` (everything else).

Both files live in :func:`hive.paths.config_dir`. Both are optional: with no
``backends.yaml`` Hive assumes one Ollama on localhost, and a built-in ``mock``
backend is always available for trying things without a model.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import paths


class ConfigError(Exception):
    """A config file is missing a field, has a bad value, or isn't valid YAML."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Timeouts(_Strict):
    """Seconds. ``first_token`` covers a cold model load, so keep it generous on slow devices."""

    connect: float = 10.0
    first_token: float = 120.0
    idle: float = 60.0
    total: float = 600.0


class ModelOverride(_Strict):
    """Per-model capability overrides inside a backend entry."""

    context: int | None = None
    tools: bool | None = None
    speed: float | None = None


class Route(_Strict):
    """A backend plus, optionally, a model on it (the backend's default otherwise)."""

    backend: str
    model: str | None = None

    def __str__(self) -> str:
        return f"{self.backend}/{self.model}" if self.model else self.backend


def _as_route(v: Any) -> Any:
    return {"backend": v} if isinstance(v, str) else v


class BackendConfig(_Strict):
    """One model server, or one device behind a shared server (see DESIGN.md §3)."""

    name: str
    type: str = "ollama"
    url: str = ""
    default_model: str = ""
    concurrency: int = Field(1, ge=1)
    context: int = Field(4096, ge=256)
    tools: bool | None = None  # None: ask the server (Ollama /api/show), else assume yes
    streaming: bool = True
    speed: float = Field(1.0, gt=0)
    device: str | None = None
    api_key: str | None = None
    api_key_env: str | None = None
    probe: bool = True
    keep_alive: str | None = None
    fallbacks: list[Route] = Field(default_factory=list)
    timeouts: Timeouts = Field(default_factory=Timeouts)
    retries: int = Field(2, ge=0)
    chars_per_token: float = Field(3.5, gt=0)
    models: dict[str, ModelOverride] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)  # adapter-specific settings

    @field_validator("fallbacks", mode="before")
    @classmethod
    def _fallbacks(cls, v: Any) -> Any:
        return [_as_route(x) for x in (v or [])]

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return v.rstrip("/")

    def key(self) -> str | None:
        """The API key, from ``api_key`` or the environment variable named by ``api_key_env``."""
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env) or None
        return None


class DeviceConfig(_Strict):
    concurrency: int = Field(1, ge=1)


class BackendsFile(_Strict):
    backends: list[BackendConfig] = Field(default_factory=list)
    devices: dict[str, DeviceConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique(self) -> "BackendsFile":
        seen: set[str] = set()
        for b in self.backends:
            if b.name in seen:
                raise ValueError(f"two backends are named {b.name!r}")
            seen.add(b.name)
        return self


class Limits(_Strict):
    """Loop protection and budgets (DESIGN.md §9). Per run; can be overridden per run."""

    max_turns_per_agent: int = 30
    max_turns_per_run: int = 150
    max_tool_steps: int = 8
    max_tokens_per_run: int = 400_000
    max_wall_seconds: int = 7200
    repeat_threshold: int = 3
    repeat_similarity: float = 0.9
    stall_turns: int = 12
    pingpong_messages: int = 6
    protocol_failures: int = 4
    max_rounds: int = 3


class ShellConfig(_Strict):
    timeout: float = 60.0
    max_output: int = 64_000
    sandbox: Literal["auto", "bwrap", "none"] = "auto"
    allow: list[str] = Field(default_factory=list)  # command prefixes that skip approval


class WebFetchConfig(_Strict):
    allowed_hosts: list[str] = Field(default_factory=list)  # empty: any host
    max_bytes: int = 500_000
    timeout: float = 20.0


class ServerConfig(_Strict):
    host: str = "127.0.0.1"
    port: int = 8765
    token: str | None = None


class HiveConfig(_Strict):
    default_backend: str | None = None
    summarizer: Route | None = None
    limits: Limits = Field(default_factory=Limits)
    shell: ShellConfig = Field(default_factory=ShellConfig)
    web_fetch: WebFetchConfig = Field(default_factory=WebFetchConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    approval_timeout: float = 900.0
    backend_down_timeout: float = 600.0
    health_interval: float = 10.0
    max_tokens: int = 1024  # reply budget per model call

    @field_validator("summarizer", mode="before")
    @classmethod
    def _summ(cls, v: Any) -> Any:
        return _as_route(v) if v else None


#: Always available unless backends.yaml defines its own ``mock``.
MOCK_BACKEND = BackendConfig(
    name="mock",
    type="mock",
    default_model="fake",
    concurrency=2,
    context=8192,
    tools=True,
    options={"latency": 0.15, "tokens_per_sec": 60},
)

def ollama_default_url() -> str:
    """Ollama's address on this machine, honouring ``OLLAMA_HOST`` like the Ollama CLI does."""
    host = os.environ.get("OLLAMA_HOST", "").strip()
    if not host:
        return "http://127.0.0.1:11434"
    if "://" not in host:
        host = "http://" + host
    host = host.replace("0.0.0.0", "127.0.0.1").rstrip("/")
    if host.count(":") == 1:  # only the scheme's colon: no port given
        host += ":11434"
    return host


#: Used when there's no backends.yaml at all.
DEFAULT_OLLAMA = BackendConfig(name="local", type="ollama", url=ollama_default_url(), concurrency=1, context=8192)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: not valid YAML ({e})") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    return data


class Settings:
    """Everything loaded from the config dir, in one object."""

    def __init__(self, backends: BackendsFile, hive: HiveConfig, config_dir: Path):
        self.backends_file = backends
        self.hive = hive
        self.config_dir = config_dir

    @property
    def backends(self) -> list[BackendConfig]:
        return self.backends_file.backends

    @property
    def devices(self) -> dict[str, DeviceConfig]:
        return self.backends_file.devices

    def backend(self, name: str) -> BackendConfig:
        for b in self.backends:
            if b.name == name:
                return b
        raise ConfigError(f"no backend named {name!r} (have: {', '.join(b.name for b in self.backends)})")

    def default_backend(self) -> str:
        if self.hive.default_backend:
            return self.hive.default_backend
        real = [b for b in self.backends if b.type != "mock"]
        return (real or self.backends)[0].name


def load_settings(config_dir: Path | None = None) -> Settings:
    """Load ``backends.yaml`` and ``hive.yaml`` from ``config_dir`` (default: the platform config dir)."""
    cdir = Path(config_dir) if config_dir else paths.config_dir()
    bpath, hpath = cdir / "backends.yaml", cdir / "hive.yaml"
    try:
        if bpath.exists():
            bfile = BackendsFile.model_validate(_read_yaml(bpath))
        else:
            bfile = BackendsFile(backends=[DEFAULT_OLLAMA])
        hive = HiveConfig.model_validate(_read_yaml(hpath)) if hpath.exists() else HiveConfig()
    except ConfigError:
        raise
    except Exception as e:  # pydantic.ValidationError, mostly
        raise ConfigError(f"{cdir}: {e}") from None
    if not any(b.name == "mock" for b in bfile.backends):
        bfile.backends.append(MOCK_BACKEND.model_copy(deep=True))
    settings = Settings(bfile, hive, cdir)
    _check_refs(settings)
    return settings


def _check_refs(s: Settings) -> None:
    names = {b.name for b in s.backends}
    for b in s.backends:
        for fb in b.fallbacks:
            if fb.backend not in names:
                raise ConfigError(f"backend {b.name!r}: fallback {fb.backend!r} isn't defined")
    if s.hive.default_backend and s.hive.default_backend not in names:
        raise ConfigError(f"hive.yaml: default_backend {s.hive.default_backend!r} isn't defined")
