"""Events and the in-process bus that fans them out (DESIGN.md §2).

A :class:`Run` writes each event to the store, then publishes it here. The UI
(SSE), the CLI printer, the loop guard and the modes all subscribe. Streaming
token deltas are published without being stored.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

log = logging.getLogger("hive.events")


@dataclass
class Event:
    run_id: str
    seq: int  # 0 for unpersisted events (token deltas, load metrics)
    ts: float
    kind: str
    agent: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Subscription:
    """An async iterator of events. If the consumer falls behind, events are dropped
    and ``dropped`` is set, so the consumer can catch up from the store by seq."""

    def __init__(self, bus: "EventBus", run_id: str | None, maxsize: int):
        self.bus = bus
        self.run_id = run_id
        self.queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize)
        self.dropped = False

    def _offer(self, ev: Event) -> None:
        try:
            self.queue.put_nowait(ev)
        except asyncio.QueueFull:
            self.dropped = True

    def close(self) -> None:
        self.bus._remove(self)
        try:
            self.queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

    def __aiter__(self) -> "Subscription":
        return self

    async def __anext__(self) -> Event:
        ev = await self.queue.get()
        if ev is None:
            raise StopAsyncIteration
        return ev

    async def get(self, timeout: float | None = None) -> Event | None:
        """The next event, or None on timeout or close."""
        try:
            return await asyncio.wait_for(self.queue.get(), timeout)
        except asyncio.TimeoutError:
            return None


Listener = Callable[[Event], Any]


class EventBus:
    def __init__(self) -> None:
        self._subs: list[Subscription] = []
        self._listeners: list[tuple[str | None, Listener]] = []

    def subscribe(self, run_id: str | None = None, maxsize: int = 10_000) -> Subscription:
        """Events for one run (or all runs when ``run_id`` is None), as an async iterator."""
        sub = Subscription(self, run_id, maxsize)
        self._subs.append(sub)
        return sub

    def listen(self, fn: Listener, run_id: str | None = None) -> Callable[[], None]:
        """Call ``fn(event)`` synchronously for each event. Returns an unsubscribe function."""
        entry = (run_id, fn)
        self._listeners.append(entry)
        return lambda: self._listeners.remove(entry) if entry in self._listeners else None

    def publish(self, ev: Event) -> None:
        for run_id, fn in list(self._listeners):
            if run_id is None or run_id == ev.run_id:
                try:
                    fn(ev)
                except Exception:
                    log.exception("event listener failed on %s", ev.kind)
        for sub in list(self._subs):
            if sub.run_id is None or sub.run_id == ev.run_id:
                sub._offer(ev)

    def _remove(self, sub: Subscription) -> None:
        if sub in self._subs:
            self._subs.remove(sub)
