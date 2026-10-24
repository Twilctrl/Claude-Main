"""Agent memory: retrieval, storage hygiene, recall into prompts, the tools, reflection, rollback, API, CLI."""

import asyncio

import httpx
import pytest

from hive.memory import Memory, MemoryStore, parse_lessons, rank, tokens
from hive.store import Store


def note(id, text, project=None, uses=0):
    return Memory(id, "coder", text, "note", "agent", project, None, float(id), uses)


# --- retrieval ------------------------------------------------------------------------------


def test_same_note():
    from hive.memory import same_note

    assert same_note("Use tabs for indentation in app.py", "use tabs for indentation in app.py.")
    assert not same_note("Tests for m1.py live in tests/", "Tests for m2.py live in tests/")
    assert not same_note("Tests do not run with pytest", "Tests run with pytest")
    assert not same_note("", "")


def test_tokens():
    assert tokens("The tests use PyTest and live in tests/test_app.py") == ["test", "use", "pytest", "live", "test", "test_app.py"]


def test_rank_relevance_and_boosts():
    notes = [
        note(1, "The README is written in British English."),
        note(2, "Run the tests with python -m pytest -q before finishing."),
        note(3, "Use tabs for indentation in app.py.", project="p2"),
        note(4, "Use tabs for indentation in app.py.", project="p1"),
        note(5, "Bananas are yellow."),
    ]
    ranked = rank("how do I run the tests", notes)
    assert ranked[0].id == 2 and all(n.id != 5 for n in ranked)
    tabs = rank("indentation in app.py", notes, project_id="p1")
    assert [n.id for n in tabs[:2]] == [4, 3]  # same project first
    assert rank("", notes) == [] and rank("zebra", notes) == []


@pytest.fixture
async def mem():
    store = await Store(":memory:").open()
    yield MemoryStore(store, max_per_agent=10)
    await store.close()


async def test_add_dedupes_and_counts_use(mem):
    a, created = await mem.add("coder", "Use tabs for indentation in app.py", project_id="p1")
    b, created2 = await mem.add("coder", "use tabs for indentation in app.py.", project_id="p1")
    assert created and not created2 and a.id == b.id
    assert (await mem.get(a.id)).uses == 1
    c, created3 = await mem.add("coder", "Use tabs for indentation in app.py", project_id="p2")
    assert created3  # a different project is a different note


async def test_cap_prunes_least_used_but_keeps_user_notes(mem):
    await mem.add("coder", "A note you wrote yourself about deploys", source="user")
    for i in range(14):
        await mem.add("coder", f"Fact number {i} about module m{i}.py and its quirks")
    kept = await mem.notes("coder")
    assert len(kept) == 10
    assert any(n.source == "user" for n in kept)
    assert not any("Fact number 0 " in n.text for n in kept)


async def test_search_delete_clear_namespaces(mem):
    await mem.add("coder", "Tests run with pytest", project_id="p1")
    await mem.add("coder", "Global: prefer small functions")
    await mem.add("writer", "Docs are in docs/")
    hits = await mem.search("coder", "pytest tests", project_id="p1")
    assert hits[0].text == "Tests run with pytest"
    assert await mem.search("coder", "pytest", project_id="p2") == []  # other project's note isn't offered
    assert [s["agent"] for s in await mem.namespaces()] == ["coder", "writer"]
    assert await mem.delete(hits[0].id) and not await mem.delete(hits[0].id)
    assert await mem.clear("coder") == 1


@pytest.mark.parametrize(
    "text,expected",
    [
        ("- Tests live in tests/.\n- Use pytest -q.\n", ["Tests live in tests/.", "Use pytest -q."]),
        ("Here you go:\n1. Read files first.\n2) Keep commits small.\n* Ask before deleting.\n- And a fourth", ["Read files first.", "Keep commits small.", "Ask before deleting."]),
        ("- none", []),
        ("Nothing to add.", []),
        ("- ok", []),  # too short to be useful
    ],
)
def test_parse_lessons(text, expected):
    assert parse_lessons(text) == expected


# --- in runs ----------------------------------------------------------------------------------------

SCRIPTED = """
backends:
  - name: mock
    type: mock
    default_model: fake
    concurrency: 4
    tools: true
    options:
      script:
        rememberer:
          - {tool: remember, args: {text: "The client wants all prices in euros."}}
          - {tool: recall, args: {query: "prices currency"}}
          - "noted"
"""


async def test_recalled_notes_are_pinned_in_the_prompt(make_app):
    app = await make_app()
    await app.memory.add("coder", "In this project, app.py must start with a licence comment.", source="user")
    project = await app.create_project("write app.py")
    run = await app.start_run(project, mode="solo", team=app.build_team(["coder"]), limits={})
    assert await run.wait(10) == "done"
    first_call = app.scheduler.backends["mock"].received[0]
    system = first_call["messages"][0].content
    assert "From your notes on earlier runs" in system and "licence comment" in system
    assert {t.name for t in first_call["tools"]} >= {"remember", "recall"}
    events = [e for e in await app.store.events(run.id) if e["kind"] == "memory.recalled"]
    assert events and events[0]["agent"] == "coder" and "licence" in events[0]["payload"]["notes"][0]["text"]


async def test_remember_and_recall_tools(make_app):
    app = await make_app(SCRIPTED, hive_yaml="memory: {reflect: false}\n")
    project = await app.create_project("price list")
    team = app.build_team([{"role": "writer", "name": "rememberer", "model": "rememberer"}])
    run = await app.start_run(project, mode="solo", team=team)
    assert await run.wait(10) == "done"
    notes = await app.memory.notes("rememberer")
    assert [(n.text, n.project_id, n.run_id, n.source) for n in notes] == [("The client wants all prices in euros.", project["id"], run.id, "agent")]
    results = [e["payload"]["output"] for e in await app.store.events(run.id) if e["kind"] == "tool.result"]
    assert results[0].startswith("Saved as note") and "euros" in results[1]


async def test_reflection_writes_lessons_and_a_later_run_recalls_them(make_app):
    app = await make_app()
    project = await app.create_project("write app.py and README.md")
    run = await app.start_run(project, mode="pipeline", team=app.build_team(["coder", "reviewer"]))
    assert await run.wait(15) == "done"
    lessons = [n for n in await app.memory.notes("coder") if n.kind == "lesson"]
    assert lessons and all(n.source == "reflection" and n.project_id == project["id"] for n in lessons)
    assert [n for n in await app.memory.notes("reviewer") if n.kind == "lesson"]
    kinds = [e["kind"] for e in await app.store.events(run.id)]
    assert kinds.index("memory.reflecting") < kinds.index("run.finished")

    run2 = await app.start_run(project, goal="update app.py", mode="solo", team=app.build_team(["coder"]))
    assert await run2.wait(15) == "done"
    recalled = [e for e in await app.store.events(run2.id) if e["kind"] == "memory.recalled"]
    assert recalled and any("app.py" in n["text"] for n in recalled[0]["payload"]["notes"])


async def test_no_reflection_when_stopped_or_switched_off(make_app):
    app = await make_app()
    project = await app.create_project("x")
    run = await app.start_run(project, mode="pipeline", team=app.build_team(["coder", "reviewer"]))
    for _ in range(200):
        if run.agents["coder"].turns:
            break
        await asyncio.sleep(0.01)
    await run.stop()
    assert await app.memory.notes("coder") == []

    app2 = await make_app(hive_yaml="memory: {reflect: false}\n")
    project = await app2.create_project("y")
    run = await app2.start_run(project, mode="solo", team=app2.build_team(["coder"]))
    await run.wait(10)
    assert await app2.memory.notes("coder") == []


async def test_memory_off_per_agent_or_globally(make_app):
    app = await make_app()
    await app.memory.add("coder", "secret sauce for app.py", source="user")
    project = await app.create_project("write app.py")
    run = await app.start_run(project, mode="solo", team=app.build_team([{"role": "coder", "memory": False}]))
    await run.wait(10)
    call = app.scheduler.backends["mock"].received[0]
    assert "secret sauce" not in call["messages"][0].content
    assert not {t.name for t in call["tools"]} & {"remember", "recall"}
    assert len(await app.memory.notes("coder")) == 1  # no lessons either

    app2 = await make_app(hive_yaml="memory: {enabled: false}\n")
    await app2.memory.add("coder", "secret sauce for app.py", source="user")
    project = await app2.create_project("write app.py")
    run = await app2.start_run(project, mode="solo")
    await run.wait(10)
    assert "secret sauce" not in app2.scheduler.backends["mock"].received[0]["messages"][0].content


async def test_shared_namespace(make_app):
    app = await make_app(hive_yaml="memory: {reflect: false}\n")
    await app.memory.add("team", "Every file needs a docstring at the top of app.py", source="user")
    project = await app.create_project("write app.py")
    team = app.build_team([{"role": "coder", "memory": "team"}])
    run = await app.start_run(project, mode="solo", team=team)
    await run.wait(10)
    assert "docstring" in app.scheduler.backends["mock"].received[0]["messages"][0].content


async def test_rollback_forgets_notes_saved_after_the_checkpoint(make_app):
    from hive.modes import MODES
    from hive.modes.base import Mode

    class Waiting(Mode):
        name = "waiting"
        waits_for_user = True

    MODES["waiting"] = Waiting
    try:
        app = await make_app()
        project = await app.create_project("notes")
        run = await app.start_run(project, mode="waiting", team=app.build_team(["coder"]))
        cp = (await app.store.checkpoints(run.id))[0]["id"]
        await run.remember(run.agents["coder"], "a fact learned after the checkpoint")
        await app.memory.add("coder", "an older note from another run", source="user")
        await run.rollback(cp)
        texts = [n.text for n in await app.memory.notes("coder")]
        assert texts == ["an older note from another run"]
        assert "memory.forgotten" in [e["kind"] for e in await app.store.events(run.id)]
        await run.stop()
    finally:
        MODES.pop("waiting", None)


# --- API and CLI ------------------------------------------------------------------------------------------


async def test_memory_api(make_app):
    from hive.web.app import create_app

    app = await make_app()
    project = await app.create_project("demo")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(app)), base_url="http://127.0.0.1") as c:
        h = {"X-Hive": "1"}
        r = await c.post("/api/memory", headers=h, json={"agent": "coder", "text": "Tests use pytest", "project": project["slug"]})
        assert r.status_code == 200 and r.json()["created"]
        await c.post("/api/memory", headers=h, json={"agent": "coder", "text": "Prefer small functions"})
        data = (await c.get("/api/memory", params={"agent": "coder", "q": "pytest"})).json()
        assert data["notes"][0]["text"] == "Tests use pytest" and data["notes"][0]["project"] == project["slug"]
        assert data["agents"] == [{"agent": "coder", "notes": 2, "latest": data["agents"][0]["latest"]}]
        nid = data["notes"][0]["id"]
        assert (await c.delete(f"/api/memory/{nid}")).status_code == 403  # no X-Hive header
        assert (await c.delete(f"/api/memory/{nid}", headers=h)).status_code == 200
        assert (await c.delete(f"/api/memory/{nid}", headers=h)).status_code == 404


def test_memory_cli(tmp_path, capsys):
    from hive.cli import main

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    assert main(["--config", str(cfg), "memory", "add", "coder", "Tests run with pytest -q"]) == 0
    assert main(["--config", str(cfg), "memory", "add", "coder", "Docs live in docs/"]) == 0
    capsys.readouterr()
    assert main(["--config", str(cfg), "memory", "list", "coder", "-s", "how to run tests"]) == 0
    out = capsys.readouterr().out
    assert "Tests run with pytest -q" in out and "Docs live" not in out
    assert main(["--config", str(cfg), "memory"]) == 0
    assert "Docs live" in capsys.readouterr().out
    assert main(["--config", str(cfg), "memory", "clear", "coder"]) == 1  # needs --yes
    assert main(["--config", str(cfg), "memory", "forget", "1"]) == 0
    assert main(["--config", str(cfg), "memory", "clear", "coder", "--yes"]) == 0
    assert "forgot 1 notes" in capsys.readouterr().out
