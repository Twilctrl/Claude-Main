"""Loop protection: nudges, pauses and budgets."""

import asyncio
import time

import pytest

from hive.modes import MODES
from hive.modes.base import Mode


class Quiet(Mode):
    name = "quiet"
    waits_for_user = True


@pytest.fixture(autouse=True)
def _mode():
    MODES["quiet"] = Quiet
    yield
    MODES.pop("quiet", None)


async def make_run(make_app, limits=None, team=("coder", "writer")):
    app = await make_app()
    project = await app.create_project("guard test")
    run = await app.start_run(project, mode="quiet", team=app.build_team(list(team)), limits=limits or {})
    return app, run


async def events(app, run, kind):
    await asyncio.sleep(0.05)
    return [e for e in await app.store.events(run.id) if e["kind"] == kind]


async def test_repeated_replies_nudge_then_pause(make_app):
    app, run = await make_run(make_app, {"repeat_threshold": 3})
    a = run.agents["coder"]
    for _ in range(3):
        run.guard.after_reply(a, "I will now do the thing.")
    assert a.history[-1].content.startswith("[Hive] You are repeating yourself")
    assert not a.paused
    for _ in range(3):
        run.guard.after_reply(a, "I will now do the thing!")  # near-identical still counts
    await asyncio.sleep(0.05)
    assert a.paused
    trig = await events(app, run, "guard.trigger")
    assert [t["payload"]["action"] for t in trig] == ["nudge", "paused"]
    assert await events(app, run, "run.attention")


async def test_different_replies_do_not_trigger(make_app):
    app, run = await make_run(make_app, {"repeat_threshold": 3, "stall_turns": 100})
    a = run.agents["coder"]
    for text in ["Reading the spec.", "Writing parser.py now.", "Tests pass; summary follows."]:
        run.guard.after_reply(a, text)
    assert not await events(app, run, "guard.trigger")


async def test_identical_tool_calls(make_app):
    app, run = await make_run(make_app, {"repeat_threshold": 3})
    a = run.agents["coder"]
    for _ in range(3):
        run.guard.on_tool_call(a, "read_file", {"path": "a.txt"})
    trig = await events(app, run, "guard.trigger")
    assert trig[0]["payload"]["rule"] == "same_tool_call"


async def test_stall_and_progress_reset(make_app):
    app, run = await make_run(make_app, {"stall_turns": 4})
    a = run.agents["coder"]
    for i in range(3):
        run.guard.after_reply(a, f"thinking {i} " + "x" * i * 30)
    run.guard.on_progress("coder")
    for i in range(3):
        run.guard.after_reply(a, f"more {i} " + "y" * i * 30)
    assert not await events(app, run, "guard.trigger")
    run.guard.after_reply(a, "and again " + "z" * 200)
    trig = await events(app, run, "guard.trigger")
    assert trig and trig[0]["payload"]["rule"] == "stall"


async def test_ping_pong(make_app):
    app, run = await make_run(make_app, {"pingpong_messages": 4})
    for i in range(4):
        run.guard.on_message("coder" if i % 2 else "writer", "writer" if i % 2 else "coder")
    trig = await events(app, run, "guard.trigger")
    assert trig and trig[0]["payload"]["rule"] == "pingpong"


async def test_protocol_failures(make_app):
    app, run = await make_run(make_app, {"protocol_failures": 2})
    a = run.agents["coder"]
    a.protocol_failures = 2
    run.guard.on_protocol_failure(a)
    trig = await events(app, run, "guard.trigger")
    assert trig[0]["payload"]["rule"] == "protocol"


async def test_budgets(make_app):
    app, run = await make_run(make_app, {"max_tokens_per_run": 100, "max_turns_per_run": 5, "max_wall_seconds": 60})
    assert run.guard.check_budget() is None
    run.tokens_used = 100
    assert "token budget" in run.guard.check_budget()
    run.tokens_used = 0
    run.turns_used = 5
    assert "turn budget" in run.guard.check_budget()
    run.turns_used = 0
    run.guard.started = time.monotonic() - 61
    assert "time budget" in run.guard.check_budget()


async def test_turn_limit_per_agent_pauses_it(make_app):
    app, run = await make_run(make_app, {"max_turns_per_agent": 1}, team=("coder",))
    await run.user_message("coder", "write a.py")
    for _ in range(100):
        if run.agents["coder"].turns == 1 and not run.agents["coder"].busy:
            break
        await asyncio.sleep(0.02)
    await run.user_message("coder", "write b.py too")
    await asyncio.sleep(0.2)
    assert run.agents["coder"].turns == 1 and run.agents["coder"].paused
    assert await events(app, run, "guard.blocked")
