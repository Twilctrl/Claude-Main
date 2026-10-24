"""Persistence: resuming runs after a crash or a budget stop, replay data, export, checkpoints."""

import asyncio

from hive.config import load_settings
from hive.export import transcript
from hive.runtime.app import Hive

from conftest import DEFAULT_BACKENDS


async def open_app(cfg, db):
    app = await Hive.open(load_settings(cfg), db=db, start_health=False)
    for b in app.scheduler.backends.values():
        b.latency = 0
    return app


async def test_interrupted_run_resumes_after_restart(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "backends.yaml").write_text(DEFAULT_BACKENDS)
    db = tmp_path / "hive.db"

    app = await open_app(cfg, db)
    project = await app.create_project("write app.py and README.md")
    for b in app.scheduler.backends.values():
        b.latency = 0.05
    run = await app.start_run(project, mode="pipeline", team=app.build_team(["planner", "coder", "reviewer"]))
    # Let it get past planning, then "crash" (close the app mid-run).
    for _ in range(400):
        if any(t.status == "done" for t in run.tasks.tasks.values() if t.id > 1):
            break
        await asyncio.sleep(0.01)
    run_id = run.id
    await app.close()
    assert (await (await open_app(cfg, db)).store.get_run(run_id))["status"] == "interrupted"

    app2 = await open_app(cfg, db)
    run2 = await app2.resume_run(run_id)
    assert await run2.wait(20) == "done"
    assert (run2.workspace.root / "app.py").exists() and (run2.workspace.root / "README.md").exists()
    events = await app2.store.events(run_id)
    kinds = [e["kind"] for e in events]
    assert "run.resumed" in kinds and kinds[-1] == "run.finished"
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    await app2.close()


async def test_budget_stop_then_resume_with_more(make_app):
    app = await make_app()
    project = await app.create_project("write a.py")
    run = await app.start_run(project, mode="pipeline", team=app.build_team(["coder", "reviewer"]), limits={"max_turns_per_run": 1})
    assert await run.wait(10) == "budget_exhausted"
    run2 = await app.resume_run(run.id, limits={"max_turns_per_run": 50})
    assert await run2.wait(10) == "done"
    assert run2.limits.max_turns_per_run == 50


async def test_cannot_resume_done_or_running(make_app):
    from hive.config import ConfigError

    app = await make_app()
    project = await app.create_project("write a.py")
    run = await app.start_run(project, mode="solo")
    await run.wait(10)
    try:
        await app.resume_run(run.id)
        raise AssertionError("expected ConfigError")
    except ConfigError as e:
        assert "finished successfully" in str(e)


async def test_events_are_enough_to_replay(make_app):
    """The UI's replay folds the stored events; check they carry what it needs."""
    app = await make_app()
    project = await app.create_project("write a.py")
    run = await app.start_run(project, mode="pipeline", team=app.build_team(["coder", "reviewer"]))
    await run.wait(10)
    events = await app.store.events(run.id)
    started = next(e for e in events if e["kind"] == "run.started")
    assert {m["name"] for m in started["payload"]["team"]} == {"coder", "reviewer"}
    tasks = {}
    for e in events:
        if e["kind"] == "task.updated":
            tasks[e["payload"]["task"]["id"]] = e["payload"]["task"]
    assert tasks == {t["id"]: t for t in await app.store.tasks(run.id)}
    files = [e for e in events if e["kind"] == "file.version"]
    assert files and files[0]["payload"]["path"] == "a.py"


async def test_export_markdown(make_app):
    app = await make_app()
    project = await app.create_project("write a.py")
    run = await app.start_run(project, mode="pipeline", team=app.build_team(["coder", "reviewer"]))
    await run.wait(10)
    md = await transcript(app.store, run.id)
    for part in ("# Hive run: write a.py", "## Team", "## Deliverable", "## Tasks", "## Timeline", "## Changes", "```diff", "+def main"):
        assert part in md, part
    assert "## Changes" not in await transcript(app.store, run.id, include_diffs=False)


async def test_checkpoints_store_full_state(make_app):
    app = await make_app()
    project = await app.create_project("write a.py")
    run = await app.start_run(project, mode="pipeline", team=app.build_team(["coder", "reviewer"]))
    await run.wait(10)
    cps = await app.store.checkpoints(run.id)
    assert cps[0]["label"] == "start" and len(cps) >= 3
    last = await app.store.get_checkpoint(cps[-1]["id"])
    st = last["state"]
    assert set(st) >= {"tasks", "board", "agents", "files", "mode", "tokens_used", "turns_used"}
    assert "a.py" in st["files"] and await app.store.get_blob(st["files"]["a.py"])


import pytest


@pytest.mark.parametrize(
    "mode,team,files",
    [
        ("pipeline", ["planner", "coder", "reviewer", "tester"], ["app.py", "README.md"]),
        ("manager", ["coordinator", "coder", "writer"], ["app.py", "README.md"]),
        ("debate", ["coder", "writer", "judge"], ["app.py"]),
    ],
)
async def test_resume_from_every_checkpoint_finishes(make_app, mode, team, files):
    """Whatever point a run stopped at, resuming from that checkpoint must still reach the goal."""
    app = await make_app()
    project = await app.create_project("write app.py and README.md")
    run = await app.start_run(project, mode=mode, team=app.build_team(team))
    assert await run.wait(20) == "done"
    cps = await app.store.checkpoints(run.id)
    assert len(cps) >= 2
    for cp in cps:
        await app.store.update_run(run.id, status="interrupted")
        resumed = await app.resume_run(run.id, checkpoint=cp["id"])
        status = await resumed.wait(20)
        assert status == "done", f"resuming from checkpoint {cp['label']!r} ended {status}"
        for f in files:
            assert (resumed.workspace.root / f).exists(), f"{f} missing after resuming from {cp['label']!r}"
