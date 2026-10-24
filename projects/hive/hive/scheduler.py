"""The scheduler: every model call goes through here (DESIGN.md §4).

- Each backend has a queue with ``concurrency`` slots, and so does each
  ``device`` that backends share. A call needs a slot in both.
- Queues are FIFO by priority. Within a priority, a request for the model the
  backend served last may jump ahead (model affinity), so one-model-at-a-time
  servers don't reload on every call. A request is jumped at most
  ``max_skips`` times.
- Calls get a total timeout, and retries with backoff on retryable errors.
- Repeated failures mark a backend down. Calls then reroute to a healthy
  fallback, or wait for the backend to come back (the health loop probes it).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import statistics
import time
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from .backends import (
    BackendDown,
    BackendError,
    BackendTimeout,
    Capabilities,
    ChatMessage,
    ChatResult,
    GenParams,
    Health,
    ModelBackend,
    TextDelta,
    ToolSpec,
    create_backend,
)
from .config import DeviceConfig, Route, Settings

log = logging.getLogger("hive.scheduler")

#: Priority for calls a person is waiting on (``hive chat``, a user's message).
PRIORITY_INTERACTIVE = 10

OnDelta = Callable[[str], Any]
OnEvent = Callable[[str, dict[str, Any]], Any]


@dataclass
class _Waiter:
    priority: int
    seq: int
    model: str
    fut: asyncio.Future[None]
    skips: int = 0


class SlotQueue:
    """A counting semaphore with priorities and model affinity."""

    def __init__(self, name: str, capacity: int, *, affinity: bool = True, max_skips: int = 3):
        self.name = name
        self.capacity = capacity
        self.affinity = affinity
        self.max_skips = max_skips
        self.in_flight = 0
        self.last_model: str | None = None
        self._waiters: list[_Waiter] = []
        self._seq = 0

    @property
    def depth(self) -> int:
        """Requests waiting for a slot."""
        return len(self._waiters)

    def busy(self) -> bool:
        return self.in_flight >= self.capacity or bool(self._waiters)

    async def acquire(self, model: str, priority: int = 0) -> None:
        if not self.busy():
            self.in_flight += 1
            self.last_model = model
            return
        self._seq += 1
        w = _Waiter(priority, self._seq, model, asyncio.get_running_loop().create_future())
        self._waiters.append(w)
        try:
            await w.fut
        except asyncio.CancelledError:
            if w.fut.done() and not w.fut.cancelled():
                self.release()  # granted at the same moment we were cancelled: hand it on
            elif w in self._waiters:
                self._waiters.remove(w)
            raise

    def release(self) -> None:
        self.in_flight -= 1
        self._dispatch()

    def _pick(self) -> _Waiter:
        top = max(w.priority for w in self._waiters)
        cands = [w for w in self._waiters if w.priority == top]
        head = cands[0]
        if self.affinity and self.last_model and head.model != self.last_model and head.skips < self.max_skips:
            for w in cands[1:]:
                if w.model == self.last_model:
                    head.skips += 1
                    return w
        return head

    def _dispatch(self) -> None:
        while self.in_flight < self.capacity and self._waiters:
            w = self._pick()
            self._waiters.remove(w)
            if w.fut.cancelled():
                continue
            self.in_flight += 1
            self.last_model = w.model
            w.fut.set_result(None)


@dataclass
class BackendStats:
    """Live state and counters for one backend, shown in the load panel."""

    health: str = "unknown"  # unknown | up | degraded | down
    consecutive_failures: int = 0
    ok: int = 0
    failed: int = 0
    retries: int = 0
    tokens_out: int = 0
    tok_s: float | None = None
    last_error: str = ""
    last_model: str | None = None
    loaded: list[str] = field(default_factory=list)
    latencies: deque[float] = field(default_factory=lambda: deque(maxlen=50))
    up_event: asyncio.Event = field(default_factory=asyncio.Event)
    next_probe: float = 0.0
    probe_backoff: float = 0.0


class Scheduler:
    """Queues, limits, retries and health for every backend."""

    def __init__(
        self,
        backends: Iterable[ModelBackend],
        devices: dict[str, DeviceConfig] | None = None,
        *,
        health_interval: float = 10.0,
        down_after: int = 3,
        backend_down_timeout: float = 600.0,
        backoff_base: float = 1.0,
    ):
        self.backends: dict[str, ModelBackend] = {b.name: b for b in backends}
        self.queues = {
            n: SlotQueue(n, b.config.concurrency, affinity=b.config.options.get("affinity", True))
            for n, b in self.backends.items()
        }
        self.device_queues: dict[str, SlotQueue] = {}
        for b in self.backends.values():
            dev = b.config.device
            if dev and dev not in self.device_queues:
                cap = (devices or {}).get(dev)
                limit = cap.concurrency if cap else max(
                    x.config.concurrency for x in self.backends.values() if x.config.device == dev
                )
                self.device_queues[dev] = SlotQueue(f"device:{dev}", limit)
        self.stats = {n: BackendStats() for n in self.backends}
        for st in self.stats.values():
            st.up_event.set()  # unknown counts as available until proven otherwise
        self.health_interval = health_interval
        self.down_after = down_after
        self.backend_down_timeout = backend_down_timeout
        self.backoff_base = backoff_base
        self.listeners: list[OnEvent] = []  # notified of backend_up / backend_down
        self._health_task: asyncio.Task[None] | None = None

    @classmethod
    def from_settings(cls, settings: Settings, client: httpx.AsyncClient | None = None) -> "Scheduler":
        return cls(
            [create_backend(b, client) for b in settings.backends],
            settings.devices,
            health_interval=settings.hive.health_interval,
            backend_down_timeout=settings.hive.backend_down_timeout,
        )

    # Lifecycle -------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background health loop."""
        if self._health_task is None:
            self._health_task = asyncio.create_task(self._health_loop(), name="hive-health")

    async def close(self) -> None:
        if self._health_task:
            self._health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._health_task
            self._health_task = None
        for b in self.backends.values():
            await b.aclose()

    # Lookups --------------------------------------------------------------------------

    def backend(self, name: str) -> ModelBackend:
        try:
            return self.backends[name]
        except KeyError:
            raise BackendError(f"no backend named {name!r}") from None

    def model_for(self, route: Route) -> str:
        return route.model or self.backend(route.backend).config.default_model

    def caps(self, route: Route) -> Capabilities:
        return self.backend(route.backend).capabilities_for(self.model_for(route))

    def available(self, route: Route) -> bool:
        return route.backend in self.backends and self.stats[route.backend].health != "down"

    # The call ---------------------------------------------------------------------------

    async def chat(
        self,
        route: Route,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        *,
        params: GenParams | None = None,
        priority: int = 0,
        fallbacks: Sequence[Route] = (),
        on_delta: OnDelta | None = None,
        on_event: OnEvent | None = None,
    ) -> ChatResult:
        """Run one chat call on ``route``, or a fallback if it's down.

        ``on_delta`` gets streamed text (passing it turns streaming on).
        ``on_event(kind, data)`` hears about queued, started, retry,
        stream_reset, rerouted and waiting_backend. Raises BackendError when
        the call fails for good, or BackendDown when every route stayed down.
        """
        emit = _safe(on_event)
        routes = _dedupe([route, *fallbacks])
        exhausted: set[str] = set()
        current: Route | None = None
        attempts = 0
        last_err: BackendError | None = None
        while True:
            r = next((x for x in routes if str(x) not in exhausted and self.available(x)), None)
            if r is None:
                waiting = [x for x in routes if str(x) not in exhausted]
                if not waiting and last_err is not None:
                    raise last_err
                emit("waiting_backend", {"backends": [x.backend for x in waiting]})
                await self._wait_any_up(waiting)
                continue
            if r != (current or routes[0]):
                why = str(last_err) if last_err else f"{(current or routes[0]).backend} is down"
                emit("rerouted", {"from": str(current or routes[0]), "to": str(r), "reason": why})
                attempts = 0
            current = r
            b = self.backends[r.backend]
            try:
                return await self._call_once(b, self.model_for(r), messages, tools, params, priority, on_delta, emit)
            except BackendError as e:
                last_err = e
                if not e.retryable:
                    raise
                attempts += 1
                if not self.available(r):
                    continue  # it just went down: pick another route, or wait
                if attempts > b.config.retries:
                    exhausted.add(str(r))
                    continue
                self.stats[b.name].retries += 1
                delay = min(20.0, self.backoff_base * 2 ** (attempts - 1)) + random.uniform(0, self.backoff_base / 2)
                emit("retry", {"backend": b.name, "attempt": attempts, "error": str(e), "delay": round(delay, 2)})
                await asyncio.sleep(delay)

    async def _call_once(
        self,
        b: ModelBackend,
        model: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None,
        params: GenParams | None,
        priority: int,
        on_delta: OnDelta | None,
        emit: OnEvent,
    ) -> ChatResult:
        q = self.queues[b.name]
        dq = self.device_queues.get(b.config.device) if b.config.device else None
        if q.busy() or (dq and dq.busy()):
            emit("queued", {"backend": b.name, "ahead": q.depth + q.in_flight})
        await q.acquire(model, priority)
        try:
            if dq:
                await dq.acquire(model, priority)
            try:
                emit("started", {"backend": b.name, "model": model})
                return await self._run(b, model, messages, tools, params, on_delta, emit)
            finally:
                if dq:
                    dq.release()
        finally:
            q.release()

    async def _run(
        self,
        b: ModelBackend,
        model: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None,
        params: GenParams | None,
        on_delta: OnDelta | None,
        emit: OnEvent,
    ) -> ChatResult:
        streamed = False
        result: ChatResult | None = None
        total = b.config.timeouts.total
        try:
            async with asyncio.timeout(total):
                async with contextlib.aclosing(
                    b.chat(messages, tools, stream=on_delta is not None, model=model, params=params)
                ) as gen:
                    async for ev in gen:
                        if isinstance(ev, TextDelta):
                            streamed = True
                            if on_delta:
                                on_delta(ev.text)
                        elif isinstance(ev, ChatResult):
                            result = ev
            if result is None:
                raise BackendError(f"{b.name}: the stream ended without a result", retryable=True)
        except TimeoutError:
            err = BackendTimeout(f"{b.name}: no complete reply within {total:g}s")
            self._failure(b.name, err)
            if streamed:
                emit("stream_reset", {"reason": str(err)})
            raise err from None
        except BackendError as e:
            if e.retryable:
                self._failure(b.name, e)
            if streamed:
                emit("stream_reset", {"reason": str(e)})
            raise
        self._success(b.name, result)
        return result

    # Health ------------------------------------------------------------------------------

    def _success(self, name: str, result: ChatResult) -> None:
        st = self.stats[name]
        was = st.health
        st.consecutive_failures = 0
        st.health = "up"
        st.up_event.set()
        st.ok += 1
        st.latencies.append(result.total_seconds * 1000)
        st.tokens_out += result.usage.completion_tokens or 0
        st.last_model = result.model
        if (tps := result.tokens_per_sec) is not None:
            st.tok_s = tps if st.tok_s is None else 0.7 * st.tok_s + 0.3 * tps
        if was == "down":
            self._notify("backend_up", {"backend": name})

    def _failure(self, name: str, err: BackendError) -> None:
        st = self.stats[name]
        st.failed += 1
        st.consecutive_failures += 1
        st.last_error = str(err)
        if st.consecutive_failures >= self.down_after:
            self._mark_down(name, str(err))
        elif st.health != "down":
            st.health = "degraded"

    def _mark_down(self, name: str, reason: str) -> None:
        st = self.stats[name]
        if st.health == "down":
            return
        st.health = "down"
        st.up_event.clear()
        st.probe_backoff = self.health_interval
        st.next_probe = time.monotonic() + st.probe_backoff
        log.info("backend %s is down: %s", name, reason)
        self._notify("backend_down", {"backend": name, "reason": reason})

    def _notify(self, kind: str, data: dict[str, Any]) -> None:
        for fn in list(self.listeners):
            _safe(fn)(kind, data)

    async def _wait_any_up(self, routes: Sequence[Route]) -> None:
        waits = [asyncio.create_task(self.stats[r.backend].up_event.wait()) for r in routes if r.backend in self.stats]
        try:
            done, _ = await asyncio.wait(waits, timeout=self.backend_down_timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in waits:
                t.cancel()
        if not done:
            names = ", ".join(r.backend for r in routes)
            raise BackendDown(f"{names}: still down after {self.backend_down_timeout:g}s")

    async def check_health(self, force: bool = False) -> dict[str, Health]:
        """Probe backends. Without ``force``, only those that are down, unknown, or idle and due."""
        now = time.monotonic()
        todo = []
        for name, b in self.backends.items():
            st = self.stats[name]
            if not force:
                if now < st.next_probe:
                    continue
                if st.health in ("up", "degraded") and self.queues[name].in_flight:
                    continue  # busy: a probe could time out on a one-request server and lie
            todo.append(b)

        async def one(b: ModelBackend) -> tuple[str, Health]:
            try:
                async with asyncio.timeout(b.config.timeouts.connect + 5):
                    return b.name, await b.health()
            except TimeoutError:
                return b.name, Health(ok=False, detail="health check timed out")
            except Exception as e:  # a buggy adapter mustn't kill the loop
                return b.name, Health(ok=False, detail=f"{type(e).__name__}: {e}")

        results = dict(await asyncio.gather(*(one(b) for b in todo)))
        now = time.monotonic()
        for name, h in results.items():
            st = self.stats[name]
            if h.ok:
                was = st.health
                st.health = "up" if was in ("down", "unknown") else st.health
                st.consecutive_failures = 0
                st.loaded = h.loaded
                st.up_event.set()
                st.next_probe = now + max(30.0, self.health_interval)
                if was == "down":
                    log.info("backend %s is back", name)
                    self._notify("backend_up", {"backend": name})
            else:
                st.last_error = h.detail
                if st.health != "down":
                    self._mark_down(name, h.detail)
                else:
                    st.probe_backoff = min(60.0, max(self.health_interval, st.probe_backoff * 2))
                    st.next_probe = now + st.probe_backoff
        return results

    async def _health_loop(self) -> None:
        while True:
            try:
                await self.check_health()
            except Exception:
                log.exception("health check failed")
            await asyncio.sleep(min(self.health_interval, 5.0))

    # Metrics -------------------------------------------------------------------------------

    def snapshot(self) -> list[dict[str, Any]]:
        """Per-backend load, for the UI and ``hive backends``."""
        out = []
        for name, b in self.backends.items():
            st, q = self.stats[name], self.queues[name]
            out.append(
                {
                    "name": name,
                    "type": b.config.type,
                    "url": b.config.url,
                    "device": b.config.device,
                    "default_model": b.config.default_model,
                    "health": st.health,
                    "queue_depth": q.depth,
                    "in_flight": q.in_flight,
                    "concurrency": q.capacity,
                    "ok": st.ok,
                    "failed": st.failed,
                    "retries": st.retries,
                    "tokens_out": st.tokens_out,
                    "tok_s": round(st.tok_s, 1) if st.tok_s is not None else None,
                    "p50_ms": round(statistics.median(st.latencies)) if st.latencies else None,
                    "last_model": st.last_model,
                    "loaded": st.loaded,
                    "last_error": st.last_error,
                }
            )
        return out


def _dedupe(routes: Sequence[Route]) -> list[Route]:
    seen: set[str] = set()
    out = []
    for r in routes:
        if str(r) not in seen:
            seen.add(str(r))
            out.append(r)
    return out


def _safe(fn: OnEvent | None) -> OnEvent:
    def call(kind: str, data: dict[str, Any]) -> None:
        if fn is None:
            return
        try:
            fn(kind, data)
        except Exception:
            log.exception("event callback failed for %s", kind)

    return call
