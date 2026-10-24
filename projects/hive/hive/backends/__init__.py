"""Model backends: the interface, the built-in adapters, and a registry for adding more.

Add a backend type by subclassing :class:`ModelBackend` and decorating it with
``@register_backend("mytype")``, or point a backend's ``type`` at
``"package.module:ClassName"`` in ``backends.yaml``.
"""

from __future__ import annotations

import importlib

import httpx

from ..config import BackendConfig, ConfigError
from .base import (
    BackendDown,
    BackendError,
    BackendTimeout,
    BackendUnavailable,
    Capabilities,
    ChatEvent,
    ChatMessage,
    ChatResult,
    GenParams,
    Health,
    ModelBackend,
    ModelInfo,
    StreamReset,
    TextDelta,
    ToolCall,
    ToolSpec,
    Usage,
)
from .mock import MockBackend
from .ollama import OllamaBackend
from .openai_compat import OpenAIBackend

_REGISTRY: dict[str, type[ModelBackend]] = {}


def register_backend(type_name: str):
    """Class decorator: make a ModelBackend subclass available as ``type: <type_name>``."""

    def deco(cls: type[ModelBackend]) -> type[ModelBackend]:
        cls.type_name = type_name
        _REGISTRY[type_name] = cls
        return cls

    return deco


for _cls in (OllamaBackend, OpenAIBackend, MockBackend):
    register_backend(_cls.type_name)(_cls)
_REGISTRY["hailo-ollama"] = OllamaBackend  # alias, for readability in YAML
_REGISTRY["llamacpp"] = OpenAIBackend
_REGISTRY["lmstudio"] = OpenAIBackend
_REGISTRY["vllm"] = OpenAIBackend


def backend_types() -> list[str]:
    return sorted(_REGISTRY)


def create_backend(config: BackendConfig, client: httpx.AsyncClient | None = None) -> ModelBackend:
    """Build the adapter named by ``config.type``."""
    cls = _REGISTRY.get(config.type)
    if cls is None and ":" in config.type:
        mod, _, attr = config.type.partition(":")
        try:
            cls = getattr(importlib.import_module(mod), attr)
        except (ImportError, AttributeError) as e:
            raise ConfigError(f"backend {config.name!r}: can't load type {config.type!r} ({e})") from None
    if cls is None:
        raise ConfigError(f"backend {config.name!r}: unknown type {config.type!r} (known: {', '.join(backend_types())})")
    return cls(config, client)


__all__ = [
    "BackendDown",
    "BackendError",
    "BackendTimeout",
    "BackendUnavailable",
    "Capabilities",
    "ChatEvent",
    "ChatMessage",
    "ChatResult",
    "GenParams",
    "Health",
    "ModelBackend",
    "ModelInfo",
    "StreamReset",
    "TextDelta",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "backend_types",
    "create_backend",
    "register_backend",
]
