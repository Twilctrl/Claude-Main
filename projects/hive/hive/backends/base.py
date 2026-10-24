"""The model backend interface and the types that cross it.

Every adapter turns one server dialect into these types. Nothing above this
layer knows whether it's talking to Ollama, an OpenAI-compatible server, or the
mock.
"""

from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar

import httpx

from ..config import BackendConfig


# --- messages and tools ------------------------------------------------------


@dataclass
class ToolSpec:
    """A tool offered to the model. ``parameters`` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str | None = None


@dataclass
class ChatMessage:
    """One chat turn. ``role`` is system, user, assistant or tool."""

    role: str
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None  # tool results answering a native call
    name: str | None = None  # tool name, on tool results

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [{"name": c.name, "arguments": c.arguments, "id": c.id} for c in self.tool_calls]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChatMessage":
        return cls(
            role=d["role"],
            content=d.get("content", ""),
            tool_calls=[ToolCall(c["name"], c.get("arguments") or {}, c.get("id")) for c in d.get("tool_calls") or []],
            tool_call_id=d.get("tool_call_id"),
            name=d.get("name"),
        )


@dataclass
class GenParams:
    """Sampling parameters. ``None`` means the server's default."""

    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    stop: list[str] | None = None
    seed: int | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "GenParams":
        d = d or {}
        return cls(**{k: d[k] for k in ("temperature", "max_tokens", "top_p", "stop", "seed") if k in d})


# --- results -------------------------------------------------------------------


@dataclass
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass
class TextDelta:
    """A piece of streamed reply text."""

    text: str


@dataclass
class StreamReset:
    """The scheduler is retrying: discard any text streamed so far for this call."""

    reason: str


@dataclass
class ChatResult:
    """The finished reply. Always the last event of :meth:`ModelBackend.chat`."""

    text: str
    tool_calls: list[ToolCall]
    usage: Usage
    model: str
    backend: str
    total_seconds: float = 0.0
    gen_seconds: float | None = None  # time spent generating, for tokens/sec
    finish_reason: str | None = None
    estimated_usage: bool = False

    @property
    def tokens_per_sec(self) -> float | None:
        if self.usage.completion_tokens and self.gen_seconds and self.gen_seconds > 0:
            return self.usage.completion_tokens / self.gen_seconds
        return None


ChatEvent = TextDelta | StreamReset | ChatResult


@dataclass
class Capabilities:
    """What a backend (or one model on it) can do."""

    max_context: int
    tool_calling: bool
    streaming: bool
    concurrency: int
    speed: float


@dataclass
class ModelInfo:
    name: str
    size: int | None = None
    context: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class Health:
    ok: bool
    latency_ms: float | None = None
    detail: str = ""
    loaded: list[str] = field(default_factory=list)


# --- errors -------------------------------------------------------------------


class BackendError(Exception):
    """A model call failed. ``retryable`` errors count against the backend's health."""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None, code: str = ""):
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.code = code  # "tools_unsupported" when a model rejects native tools


class BackendUnavailable(BackendError):
    """Couldn't connect, or the connection dropped."""

    def __init__(self, message: str):
        super().__init__(message, retryable=True)


class BackendTimeout(BackendError):
    def __init__(self, message: str):
        super().__init__(message, retryable=True)


class BackendDown(BackendError):
    """Every route for a call stayed down past the wait limit."""

    def __init__(self, message: str):
        super().__init__(message, retryable=False)


def http_error(resp_status: int, body: str, backend: str) -> BackendError:
    """Map an HTTP error status to a BackendError, pulling a message out of a JSON body."""
    msg = body.strip()
    try:
        j = json.loads(body)
        err = j.get("error", j) if isinstance(j, dict) else j
        if isinstance(err, dict):
            err = err.get("message", err)
        msg = str(err)
    except (ValueError, AttributeError):
        pass
    retryable = resp_status == 429 or resp_status >= 500
    code = "tools_unsupported" if resp_status == 400 and "support tools" in msg.lower() else ""
    return BackendError(f"{backend}: HTTP {resp_status}: {msg[:300]}", retryable=retryable, status=resp_status, code=code)


# --- the interface ----------------------------------------------------------------


class ModelBackend(ABC):
    """A model server. Subclass this and register it with :func:`hive.backends.register_backend`."""

    type_name: ClassVar[str] = ""

    def __init__(self, config: BackendConfig, client: httpx.AsyncClient | None = None):
        self.config = config
        self.name = config.name
        self._client = client
        self._owns_client = client is None
        self._model_caps: dict[str, Capabilities] = {}

    # Capabilities ---------------------------------------------------------------

    @property
    def capabilities(self) -> Capabilities:
        c = self.config
        return Capabilities(
            max_context=c.context,
            tool_calling=c.tools if c.tools is not None else True,
            streaming=c.streaming,
            concurrency=c.concurrency,
            speed=c.speed,
        )

    def capabilities_for(self, model: str | None) -> Capabilities:
        """Capabilities for one model: probed values, then config overrides, then backend defaults."""
        model = model or self.config.default_model
        caps = self._model_caps.get(model, self.capabilities)
        if ov := self.config.models.get(model):
            caps = replace(
                caps,
                max_context=ov.context or caps.max_context,
                tool_calling=caps.tool_calling if ov.tools is None else ov.tools,
                speed=ov.speed or caps.speed,
            )
        return caps

    async def probe(self, model: str | None = None) -> Capabilities:
        """Ask the server what ``model`` supports and cache it. Default: use the config."""
        return self.capabilities_for(model)

    # Calls ---------------------------------------------------------------------------

    @abstractmethod
    def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        stream: bool = False,
        *,
        model: str | None = None,
        params: GenParams | None = None,
    ) -> AsyncIterator[ChatEvent]:
        """Run one chat completion.

        Yields :class:`TextDelta` events while the reply streams (only when
        ``stream`` is true), then exactly one :class:`ChatResult`. Raises
        :class:`BackendError` on failure. Pass ``tools`` only if
        ``capabilities_for(model).tool_calling`` is true; otherwise use the
        text protocol in :mod:`hive.toolproto`.
        """

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """Models the server has available."""

    @abstractmethod
    async def health(self) -> Health:
        """A cheap liveness check."""

    # Helpers for adapters ---------------------------------------------------------------

    def model_name(self, model: str | None) -> str:
        m = model or self.config.default_model
        if not m:
            raise BackendError(f"{self.name}: no model given and no default_model set", retryable=False)
        return m

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            t = self.config.timeouts
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=t.connect, read=t.first_token, write=30.0, pool=None),
            )
        return self._client

    def _headers(self) -> dict[str, str]:
        key = self.config.key()
        return {"Authorization": f"Bearer {key}"} if key else {}

    def estimate_tokens(self, text: str) -> int:
        return max(1, int(len(text) / self.config.chars_per_token))

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None


async def with_timeouts(it: AsyncIterator[Any], first: float, idle: float, label: str) -> AsyncIterator[Any]:
    """Re-yield ``it``, failing with BackendTimeout if the first item or any later gap is too slow."""
    ait = it.__aiter__()
    wait = first
    while True:
        try:
            async with asyncio.timeout(wait):
                item = await ait.__anext__()
        except StopAsyncIteration:
            return
        except TimeoutError:
            what = "first token" if wait == first else "next token"
            raise BackendTimeout(f"{label}: no {what} within {wait:g}s") from None
        wait = idle
        yield item


class transport_errors:
    """``with transport_errors(name):`` turns httpx transport errors into BackendErrors."""

    def __init__(self, label: str):
        self.label = label

    def __enter__(self) -> "transport_errors":
        return self

    def __exit__(self, et: type[BaseException] | None, e: BaseException | None, tb: Any) -> bool:
        if et is None:
            return False
        if issubclass(et, httpx.TimeoutException):
            raise BackendTimeout(f"{self.label}: timed out ({et.__name__})") from None
        if issubclass(et, httpx.TransportError):
            raise BackendUnavailable(f"{self.label}: {et.__name__}: {e}") from None
        return False


class Stopwatch:
    """Measures total time and generation time (first token to last)."""

    def __init__(self) -> None:
        self.start = time.monotonic()
        self.first: float | None = None

    def tick(self) -> None:
        if self.first is None:
            self.first = time.monotonic()

    @property
    def total(self) -> float:
        return time.monotonic() - self.start

    @property
    def gen(self) -> float | None:
        return time.monotonic() - self.first if self.first is not None else None
