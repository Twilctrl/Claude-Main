"""Scheduler: limits, queue order, retries, timeouts, health, rerouting."""

import asyncio

import pytest

from hive.backends import BackendDown, BackendError, ChatMessage, create_backend
from hive.config import DeviceConfig, Route
from hive.scheduler import Scheduler, SlotQueue

from conftest import fast_timeouts, mock_config

MSG = [ChatMessage("user", "hi")]


def sched(*configs, devices=None, **kw):
    kw.setdefault("backoff_base", 0.01)
    kw.setdefault("health_interval", 0.05)
    kw.setdefault("backend_down_timeout", 2)
    return Scheduler([create_backend(c) for c in configs], devices, **kw)


class Recorder:
    def __init__(self):
        self.events = []

    def __call__(self, kind, data):
        self.events.append((kind, data))

    def kinds(self):
        return [k for k, _ in self.events]


async def test_concurrency_limit_is_never_exceeded():
    s = sched(mock_config("m", concurrency=2, options={"latency": 0.02}))
    peak = 0
    orig = s.backends["m"].chat

    async def watch(*a, **k):
        nonlocal peak
        peak = max(peak, s.queues["m"].in_flight)
        async for ev in orig(*a, **k):
            yield ev

    s.backends["m"].chat = watch
    results = await asyncio.gather(*(s.chat(Route(backend="m"), MSG) for _ in range(10)))
    assert len(results) == 10 and peak == 2
    assert s.queues["m"].in_flight == 0 and s.queues["m"].depth == 0
    assert s.snapshot()[0]["ok"] == 10


async def test_device_limit_shared_across_backends():
    a = mock_config("a", concurrency=2, device="gpu", options={"latency": 0.02})
    b = mock_config("b", concurrency=2, device="gpu", options={"latency": 0.02})
    s = sched(a, b, devices={"gpu": DeviceConfig(concurrency=1)})
    dq = s.device_queues["gpu"]
    peak = 0

    async def poll():
        nonlocal peak
        while True:
            peak = max(peak, dq.in_flight)
            await asyncio.sleep(0.002)

    t = asyncio.create_task(poll())
    await asyncio.gather(*(s.chat(Route(backend=n), MSG) for n in "abab"))
    t.cancel()
    assert peak == 1


async def test_slot_queue_priority_and_affinity():
    q = SlotQueue("q", 1, max_skips=1)
    await q.acquire("A")
    order = []

    async def want(model, prio=0):
        await q.acquire(model, prio)
        order.append(model)
        q.release()

    tasks = [asyncio.create_task(want(m)) for m in ["B1", "A", "B2", "A"]]
    await asyncio.sleep(0)
    tasks.append(asyncio.create_task(want("P", prio=10)))
    await asyncio.sleep(0)
    q.release()
    await asyncio.gather(*tasks)
    # P has priority. B1 is next in line (nothing queued matches P). After A is
    # served, B2 is jumped once by the other A (max_skips=1).
    assert order == ["P", "B1", "A", "A", "B2"]


async def test_affinity_prefers_loaded_model():
    q = SlotQueue("q", 1, max_skips=3)
    await q.acquire("A")
    order = []

    async def want(model):
        await q.acquire(model)
        order.append(model)
        q.release()

    tasks = [asyncio.create_task(want(m)) for m in ["B", "A", "A"]]
    await asyncio.sleep(0)
    q.release()
    await asyncio.gather(*tasks)
    assert order == ["A", "A", "B"]


async def test_affinity_skip_is_bounded():
    q = SlotQueue("q", 1, max_skips=2)
    await q.acquire("A")
    order = []

    async def want(model):
        await q.acquire(model)
        order.append(model)
        q.release()

    tasks = [asyncio.create_task(want(m)) for m in ["B", "A", "A", "A", "A"]]
    await asyncio.sleep(0)
    q.release()
    await asyncio.gather(*tasks)
    assert order.index("B") == 2  # jumped twice, then served


async def test_cancelled_waiter_releases_nothing_and_leaves_queue():
    q = SlotQueue("q", 1)
    await q.acquire("A")
    t = asyncio.create_task(q.acquire("B"))
    await asyncio.sleep(0)
    assert q.depth == 1
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    assert q.depth == 0
    q.release()
    assert q.in_flight == 0


async def test_retry_then_success():
    s = sched(mock_config("m", options={"fail_calls": [1, 2]}))
    rec = Recorder()
    res = await s.chat(Route(backend="m"), MSG, on_event=rec)
    assert res.text == "echo: hi"
    assert rec.kinds().count("retry") == 2
    st = s.snapshot()[0]
    assert st["failed"] == 2 and st["ok"] == 1 and st["health"] == "up"


async def test_non_retryable_error_is_not_retried():
    s = sched(mock_config("m", options={"fail_calls": [1], "fail_status": 404}))
    rec = Recorder()
    with pytest.raises(BackendError) as ei:
        await s.chat(Route(backend="m"), MSG, on_event=rec)
    assert ei.value.status == 404 and "retry" not in rec.kinds()
    assert s.stats["m"].health != "down"


async def test_retries_exhausted_raises():
    s = sched(mock_config("m", retries=1, options={"fail_calls": [1, 2, 3]}), down_after=10)
    with pytest.raises(BackendError, match="injected"):
        await s.chat(Route(backend="m"), MSG)
    assert s.backends["m"].calls == 2


async def test_total_timeout_counts_as_failure_and_retries():
    cfg = mock_config("m", timeouts=fast_timeouts(total=0.1), options={"hang_calls": [1]})
    s = sched(cfg)
    rec = Recorder()
    res = await s.chat(Route(backend="m"), MSG, on_event=rec)
    assert res.text == "echo: hi" and "retry" in rec.kinds()
    assert "no complete reply" in s.stats["m"].last_error


async def test_first_token_timeout():
    cfg = mock_config("m", retries=0, timeouts=fast_timeouts(total=5))
    s = sched(cfg)
    b = s.backends["m"]

    async def slow(*a, **k):
        from hive.backends.base import with_timeouts

        async def never():
            await asyncio.sleep(10)
            yield 1

        async for x in with_timeouts(never(), 0.05, 1, "m"):
            yield x

    b.chat = slow
    with pytest.raises(BackendError, match="first token"):
        await s.chat(Route(backend="m"), MSG)


async def test_stream_reset_when_failing_mid_stream():
    s = sched(mock_config("m"))
    b = s.backends["m"]
    calls = {"n": 0}
    orig = b.chat

    async def flaky(*a, **k):
        calls["n"] += 1
        from hive.backends import BackendUnavailable, TextDelta

        if calls["n"] == 1:
            yield TextDelta("partial ")
            raise BackendUnavailable("dropped")
        async for ev in orig(*a, **k):
            yield ev

    b.chat = flaky
    rec, deltas = Recorder(), []
    res = await s.chat(Route(backend="m"), MSG, on_delta=deltas.append, on_event=rec)
    assert "stream_reset" in rec.kinds()
    assert res.text == "echo: hi" and deltas[0] == "partial "


async def test_down_backend_reroutes_to_fallback():
    primary = mock_config("pi", retries=5, options={"fail_calls": [1, 2, 3, 4, 5, 6]})
    backup = mock_config("desk")
    s = sched(primary, backup, down_after=3)
    rec = Recorder()
    res = await s.chat(Route(backend="pi"), MSG, fallbacks=[Route(backend="desk")], on_event=rec)
    assert res.backend == "desk"
    assert s.stats["pi"].health == "down"
    reroute = [d for k, d in rec.events if k == "rerouted"]
    assert reroute and reroute[0]["to"] == "desk"
    # Already down: the next call goes straight to the fallback.
    rec2 = Recorder()
    res2 = await s.chat(Route(backend="pi"), MSG, fallbacks=[Route(backend="desk")], on_event=rec2)
    assert res2.backend == "desk" and rec2.kinds()[0] == "rerouted"


async def test_retries_exhausted_moves_to_fallback_without_down():
    primary = mock_config("pi", retries=1, options={"fail_calls": [1, 2]})
    s = sched(primary, mock_config("desk"), down_after=10)
    res = await s.chat(Route(backend="pi"), MSG, fallbacks=[Route(backend="desk")])
    assert res.backend == "desk" and s.stats["pi"].health == "degraded"


async def test_waits_for_backend_to_come_back():
    s = sched(mock_config("pi"), backend_down_timeout=5)
    b = s.backends["pi"]
    b.down = True
    await s.check_health(force=True)
    assert s.stats["pi"].health == "down"
    await s.start()
    rec = Recorder()

    async def revive():
        await asyncio.sleep(0.1)
        b.down = False

    asyncio.create_task(revive())
    s.stats["pi"].next_probe = 0
    res = await asyncio.wait_for(s.chat(Route(backend="pi"), MSG, on_event=rec), 3)
    await s.close()
    assert res.text == "echo: hi"
    assert "waiting_backend" in rec.kinds()
    assert s.stats["pi"].health == "up"


async def test_backend_down_timeout():
    s = sched(mock_config("pi"), backend_down_timeout=0.1)
    s.backends["pi"].down = True
    await s.check_health(force=True)
    with pytest.raises(BackendDown):
        await s.chat(Route(backend="pi"), MSG)


async def test_listeners_hear_down_and_up():
    s = sched(mock_config("pi"))
    rec = Recorder()
    s.listeners.append(rec)
    s.backends["pi"].down = True
    await s.check_health(force=True)
    s.backends["pi"].down = False
    await s.check_health(force=True)
    assert rec.kinds() == ["backend_down", "backend_up"]


async def test_busy_backend_not_probed():
    s = sched(mock_config("pi", options={"latency": 0.2}))
    t = asyncio.create_task(s.chat(Route(backend="pi"), MSG))
    await asyncio.sleep(0.05)
    s.stats["pi"].health = "up"
    probed = await s.check_health()
    await t
    assert "pi" not in probed


async def test_queued_event_and_metrics():
    s = sched(mock_config("m", concurrency=1, options={"latency": 0.02, "tokens_per_sec": 1000}))
    recs = [Recorder() for _ in range(3)]
    await asyncio.gather(*(s.chat(Route(backend="m"), MSG, on_event=r) for r in recs))
    assert sum("queued" in r.kinds() for r in recs) == 2
    snap = s.snapshot()[0]
    assert snap["ok"] == 3 and snap["p50_ms"] is not None and snap["tok_s"] is not None
