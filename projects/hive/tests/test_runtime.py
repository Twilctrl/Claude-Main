"""The multi-agent runtime on the mock backend: runs, messaging, approvals, steering."""

import asyncio

import pytest

from hive.modes import MODES
from hive.modes.base import Mode

SCRIPTED = """
backends:
  - name: mock
    type: mock
    default_model: fake
    concurrency: 4
    tools: true
    options:
      script:
        alice:
          - {tool: send_message, args: {to: bob, text: "what's 2+2?"}}
          - "I asked bob."
          - "Thanks, bob."
        bob:
          - "4"
        deleter:
          - {tool: delete_file, args: {path: doomed.txt}}
          - "Tried to delete it."
        repeater:
          - "same answer"
          - "same answer"
          - "same answer"
          - "same answer"
"""


class Idle(Mode):
    """A test mode that hands out no work: everything comes from user messages."""

    name = "idle"
    waits_for_user = True


class Nothing(Mode):
    """A broken mode that never hands out work."""

    name = "nothing"


async def settle(run, cond, timeout=5.0):
    """Wait until ``cond()`` holds and the team is idle."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if cond() and run.all_idle():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached")


@pytest.fixture(autouse=True)
def _idle_mode():
    MODES["idle"] = Idle
    MODES["nothing"] = Nothing
    yield
    MODES.pop("idle", None)
    MODES.pop("nothing", None)


async def run_to_end(app, goal, mode="solo", team=None, timeout=10, **kw):
    project = await app.create_project(goal)
    run = await app.start_run(project, goal=goal, mode=mode, team=app.build_team(team or ["coder"]), **kw)
    await run.wait(timeout)
    return run


@pytest.mark.parametrize("backend", ["mock", "text"])
async def test_solo_run_writes_file_and_delivers(make_app, backend):
    app = await make_app()
    project = await app.create_project("write hello.py")
    run = await app.start_run(project, mode="solo", team=app.build_team([f"coder@{backend}"]))
    assert await run.wait(10) == "done"
    assert (run.workspace.root / "hello.py").exists()
    assert "hello.py" in run.summary
    assert run.board.latest("deliverable") is not None
    events = await app.store.events(run.id)
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "run.status" and "run.started" in kinds and kinds[-1] == "run.finished"
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert (await app.store.get_run(run.id))["status"] == "done"
    tasks = await app.store.tasks(run.id)
    assert tasks[0]["status"] == "done" and tasks[0]["owner"] == "coder"
    if backend == "text":
        assert run.agents["coder"].native_tools() is False


async def test_reply_depth_stops_thank_you_loops(make_app):
    app = await make_app(SCRIPTED)
    project = await app.create_project("chat")
    team = app.build_team([
        {"role": "writer", "name": "alice", "model": "alice", "tools": ["send_message"]},
        {"role": "writer", "name": "bob", "model": "bob", "tools": ["send_message"]},
    ])
    run = await app.start_run(project, mode="idle", team=team)
    await run.user_message("alice", "ask bob something")
    alice, bob = run.agents["alice"], run.agents["bob"]
    await settle(run, lambda: alice.turns == 2)
    await asyncio.sleep(0.1)
    assert not run.done.is_set()  # an idle team in a waits_for_user mode keeps the run open
    idles = [e for e in await app.store.events(run.id) if e["kind"] == "run.idle"]
    assert 1 <= len(idles) <= 3  # reported once per quiet spell, not in a loop
    assert alice.turns == 2 and bob.turns == 1
    assert any("Thanks, bob." in d for d in bob.digest)
    msgs = await app.store.messages(run.id)
    assert [(m["sender"], m["recipient"], m["depth"]) for m in msgs] == [
        ("user", "alice", 0), ("alice", "bob", 0), ("alice", "user", 0), ("bob", "alice", 1), ("alice", "bob", 2),
    ]


@pytest.mark.parametrize("policy,deleted", [("all", True), ("none", False)])
async def test_approval_policies(make_app, policy, deleted):
    app = await make_app(SCRIPTED)
    project = await app.create_project("tidy up")
    (lambda p: (p.mkdir(parents=True, exist_ok=True), (p / "doomed.txt").write_text("bye")))(__import__("pathlib").Path(project["workspace"]))
    team = app.build_team([{"role": "coder", "name": "deleter", "model": "deleter", "tools": ["delete_file"]}])
    run = await app.start_run(project, mode="solo", team=team, approval=policy)
    await run.wait(10)
    assert (run.workspace.root / "doomed.txt").exists() is not deleted


async def test_approval_asked_and_granted(make_app):
    app = await make_app(SCRIPTED)
    project = await app.create_project("tidy up")
    from pathlib import Path

    Path(project["workspace"], "doomed.txt").write_text("bye")
    team = app.build_team([{"role": "coder", "name": "deleter", "model": "deleter", "tools": ["delete_file"]}])
    sub = app.subscribe()
    run = await app.start_run(project, mode="solo", team=team, approval="ask")
    async for ev in sub:
        if ev.kind == "approval.requested":
            assert ev.payload["tool"] == "delete_file"
            assert run.agents["deleter"].status == "waiting_approval"
            assert await run.approvals.decide(ev.payload["id"], True)
            break
    sub.close()
    assert await run.wait(10) == "done"
    assert not (run.workspace.root / "doomed.txt").exists()


async def test_approval_timeout_rejects(make_app):
    app = await make_app(SCRIPTED, hive_yaml="approval_timeout: 0.1\n")
    project = await app.create_project("tidy up")
    from pathlib import Path

    Path(project["workspace"], "doomed.txt").write_text("bye")
    team = app.build_team([{"role": "coder", "name": "deleter", "model": "deleter", "tools": ["delete_file"]}])
    run = await app.start_run(project, mode="solo", team=team, approval="ask")
    await run.wait(10)
    assert (run.workspace.root / "doomed.txt").exists()
    decided = [e for e in await app.store.events(run.id) if e["kind"] == "approval.decided"]
    assert decided and decided[0]["payload"]["by"] == "timeout"


async def test_pause_and_resume_agent(make_app):
    app = await make_app()
    project = await app.create_project("write a.py")
    team = app.build_team(["coder"])
    run = app.runs  # noqa: F841
    run = await app.start_run(project, mode="idle", team=team)
    await run.pause("coder")
    await run.user_message("coder", "write a.py please")
    await asyncio.sleep(0.2)
    assert run.agents["coder"].turns == 0 and run.agents["coder"].status == "paused"
    await run.resume("coder")
    await settle(run, lambda: run.agents["coder"].turns == 1)
    assert (run.workspace.root / "a.py").exists()


async def test_token_budget_ends_run(make_app):
    app = await make_app()
    run = await run_to_end(app, "write a.py", limits={"max_tokens_per_run": 10})
    assert run.status == "budget_exhausted"


async def test_failed_model_pauses_agent_and_asks(make_app):
    app = await make_app(
        "backends:\n  - {name: mock, type: mock, default_model: fake, tools: true, options: {fail_calls: [1], fail_status: 404}}\n"
    )
    project = await app.create_project("write a.py")
    sub = app.subscribe()
    run = await app.start_run(project, mode="solo", team=app.build_team(["coder"]))
    async for ev in sub:
        if ev.kind == "run.attention":
            assert "404" in ev.payload["message"]
            break
    sub.close()
    assert run.agents["coder"].paused and not run.done.is_set()
    await run.resume()
    assert await run.wait(10) == "done"


async def test_stuck_run_ends_needing_attention(make_app):
    app = await make_app()
    run = await run_to_end(app, "nothing to do", mode="nothing")
    assert run.status == "needs_attention"


async def test_edit_agent_mid_run(make_app):
    app = await make_app()
    project = await app.create_project("x")
    run = await app.start_run(project, mode="idle", team=app.build_team(["coder"]))
    spec = await run.update_agent("coder", system_prompt="You are terse.", params={"temperature": 0})
    assert spec.system_prompt == "You are terse." and run.agents["coder"].spec.params == {"temperature": 0}
    with pytest.raises(ValueError):
        await run.update_agent("coder", role="hacker")
    await run.stop()
    assert run.status == "stopped"


async def test_build_team_parsing(make_app):
    app = await make_app()
    team = app.build_team(["planner", "coder@text", "rev=reviewer@mock:big-model", "coder"])
    assert [(s.name, s.role, s.backend, s.model) for s in team] == [
        ("planner", "planner", "mock", None),
        ("coder", "coder", "text", None),
        ("rev", "reviewer", "mock", "big-model"),
        ("coder2", "coder", "mock", None),
    ]
    from hive.config import ConfigError

    with pytest.raises(ConfigError, match="unknown role"):
        app.build_team(["wizard"])
    with pytest.raises(ConfigError, match="no backend"):
        app.build_team(["coder@nowhere"])
