"""End-to-end runs of each collaboration mode on the fake model (no real LLM)."""

import asyncio

import pytest

SCRIPTED = """
backends:
  - name: mock
    type: mock
    default_model: fake
    concurrency: 4
    tools: true
    options:
      script:
        mute-reviewer:
          - "Looks good to me."
        picky:
          - {tool: request_changes, args: {notes: "1. Rename it."}}
          - {tool: request_changes, args: {notes: "1. Rename it again."}}
        lazy-coordinator:
          - "I'll think about it."
          - "Still thinking."
          - {tool: finish_run, args: {summary: "done by the fallback worker"}}
"""


async def start(app, goal, mode, team=None, **kw):
    project = await app.create_project(goal)
    return await app.start_run(project, goal=goal, mode=mode, team=app.build_team(team) if team else None, **kw)


@pytest.mark.parametrize("backend", ["mock", "text"])
async def test_pipeline_end_to_end(make_app, backend):
    """Planner → coder → reviewer (asks for changes once) → tester, with the fake model."""
    app = await make_app()
    run = await start(app, "write app.py and README.md", "pipeline", [f"{r}@{backend}" for r in ("planner", "coder", "reviewer", "tester")])
    assert await run.wait(20) == "done"
    ws = run.workspace.root
    assert (ws / "app.py").exists() and (ws / "README.md").exists()
    assert "Revised after review" in (ws / "app.py").read_text() or "Revised after review" in (ws / "README.md").read_text()
    tasks = list(run.tasks.tasks.values())
    assert [t.title for t in tasks] == ["Plan the work", "Write app.py", "Write README.md", "Address review feedback (round 2)"]
    assert all(t.status == "done" for t in tasks)
    events = await app.store.events(run.id)
    verdicts = [(e["agent"], e["payload"]["approved"]) for e in events if e["kind"] == "mode.verdict"]
    assert verdicts == [("reviewer", False), ("reviewer", True), ("tester", True)]
    assert "reviewer approved" in run.summary and "app.py" in run.summary
    assert len(await app.store.checkpoints(run.id)) >= 4


async def test_pipeline_gives_up_after_max_rounds(make_app):
    app = await make_app(SCRIPTED)
    team = ["coder", {"role": "reviewer", "name": "picky", "model": "picky"}]
    project = await app.create_project("write a.py")
    run = await app.start_run(project, mode="pipeline", team=app.build_team(team), limits={"max_rounds": 2})
    assert await run.wait(20) == "needs_attention"
    finished = [e for e in await app.store.events(run.id) if e["kind"] == "run.finished"][0]
    assert "still wants changes after 2 rounds" in finished["payload"]["reason"]


async def test_pipeline_reads_verdict_from_plain_text(make_app):
    app = await make_app(SCRIPTED)
    project = await app.create_project("write a.py")
    team = app.build_team(["coder", {"role": "reviewer", "name": "rev", "model": "mute-reviewer"}])
    run = await app.start_run(project, mode="pipeline", team=team)
    assert await run.wait(20) == "done"


@pytest.mark.parametrize("backend", ["mock", "text"])
async def test_debate_end_to_end(make_app, backend):
    app = await make_app()
    run = await start(app, "pick a name and write it to name.txt", "debate", [f"coder@{backend}", f"writer@{backend}", f"judge@{backend}"])
    assert await run.wait(20) == "done"
    phases = [e["payload"]["phase"] for e in await app.store.events(run.id) if e["kind"] == "mode.phase"]
    assert phases == ["propose", "critique", "revise", "judge"]
    kinds = [e.kind for e in run.board.entries]
    assert kinds.count("artifact") == 4 and kinds.count("note") == 2 and kinds[-1] == "deliverable"
    assert (run.workspace.root / "name.txt").exists()


async def test_debate_zero_rounds_goes_straight_to_judge(make_app):
    app = await make_app()
    run = await start(app, "answer", "debate", ["coder", "coder", "judge"], limits={"debate_rounds": 0})
    await run.wait(20)
    phases = [e["payload"]["phase"] for e in await app.store.events(run.id) if e["kind"] == "mode.phase"]
    assert phases == ["propose", "judge"]


@pytest.mark.parametrize("backend", ["mock", "text"])
async def test_manager_runs_workers_in_parallel(make_app, backend):
    app = await make_app()
    project = await app.create_project("write api.py and docs.md")
    for b in app.scheduler.backends.values():
        b.latency = 0.05  # long enough for the two workers to overlap
    run = await app.start_run(project, mode="manager", team=app.build_team([f"coordinator@{backend}", f"coder@{backend}", f"writer@{backend}"]))
    overlap = False

    async def watch():
        nonlocal overlap
        while not run.done.is_set():
            if run.agents["coder"].in_turn and run.agents["writer"].in_turn:
                overlap = True
            await asyncio.sleep(0.005)

    w = asyncio.create_task(watch())
    assert await run.wait(20) == "done"
    w.cancel()
    assert overlap, "the two workers should have worked at the same time"
    assert (run.workspace.root / "api.py").exists() and (run.workspace.root / "docs.md").exists()
    owners = {t.owner for t in run.tasks.tasks.values()}
    assert owners == {"coder", "writer"}


async def test_manager_falls_back_when_coordinator_makes_no_tasks(make_app):
    app = await make_app(SCRIPTED)
    project = await app.create_project("write a.py")
    team = app.build_team([{"role": "coordinator", "name": "boss", "model": "lazy-coordinator"}, "coder"])
    run = await app.start_run(project, mode="manager", team=team)
    assert await run.wait(20) == "done"
    events = await app.store.events(run.id)
    assert any(e["kind"] == "mode.fallback" for e in events)
    assert run.tasks.tasks[1].owner == "coder" and (run.workspace.root / "a.py").exists()


async def test_manager_open_tasks_are_dispatched_to_idle_workers(make_app):
    app = await make_app()
    project = await app.create_project("x")
    run = await app.start_run(project, mode="manager", team=app.build_team(["coordinator", "coder", "writer"]))
    await run.wait(20)
    # Unassigned tasks created later still get picked up by an idle worker.
    app2 = await make_app()
    project = await app2.create_project("y")
    run2 = await app2.start_run(project, mode="manager", team=app2.build_team(["coordinator", "coder"]))
    t = await run2.tasks.create("Write extra.md", "Create extra.md", created_by="user")
    await run2.wait(20)
    assert run2.tasks.tasks[t.id].owner == "coder"
