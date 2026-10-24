"""The web server: security checks, run lifecycle over the API, the event stream, export."""

import asyncio
import json

import httpx
import pytest

from hive.web.app import create_app, host_of, is_loopback

H = {"X-Hive": "1"}


@pytest.fixture
async def client(make_app):
    app = await make_app()
    web = create_app(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web), base_url="http://127.0.0.1:8765") as c:
        c.hive = app
        yield c


async def wait_status(c, run_id, want=("done",), timeout=15):
    for _ in range(int(timeout / 0.05)):
        r = (await c.get(f"/api/runs/{run_id}")).json()
        if r["status"] in want:
            return r
        await asyncio.sleep(0.05)
    raise AssertionError(f"run stayed {r['status']}")


def test_host_helpers():
    assert host_of("localhost:8765") == "localhost" and host_of("[::1]:8765") == "[::1]" and host_of("pi5.local") == "pi5.local"
    assert is_loopback("127.0.0.1") and is_loopback("[::1]") and is_loopback("localhost") and not is_loopback("192.168.1.5")


async def test_security_checks(client):
    assert (await client.get("/")).status_code == 200
    assert (await client.post("/api/runs", json={"goal": "x"})).status_code == 403  # no X-Hive header
    assert (await client.get("/api/info", headers={"Host": "evil.example.com"})).status_code == 403  # DNS rebinding
    assert (await client.get("/api/info")).status_code == 200  # reads don't need the header


async def test_token_required_beyond_localhost(make_app):
    app = await make_app()
    web = create_app(app, token="s3cret", host="0.0.0.0")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web), base_url="http://192.168.1.9:8765") as c:
        assert (await c.get("/api/info")).status_code == 401
        assert (await c.get("/api/info", headers={"Authorization": "Bearer nope"})).status_code == 401
        assert (await c.get("/api/info", headers={"Authorization": "Bearer s3cret"})).status_code == 200
        r = await c.get("/?token=s3cret", follow_redirects=False)
        assert r.status_code == 307 and "hive_token=s3cret" in r.headers["set-cookie"]


async def test_run_through_the_api(client):
    info = (await client.get("/api/info")).json()
    assert {m["name"] for m in info["modes"]} >= {"pipeline", "debate", "manager"}
    r = await client.post("/api/runs", headers=H, json={"goal": "write app.py", "mode": "pipeline", "team": [{"role": "coder"}, {"role": "reviewer"}]})
    assert r.status_code == 200, r.text
    run_id = r.json()["id"]
    detail = await wait_status(client, run_id)
    assert detail["summary"] and detail["checkpoints"]
    events = (await client.get(f"/api/runs/{run_id}/events")).json()
    assert events[-1]["kind"] == "run.finished"
    files = (await client.get(f"/api/runs/{run_id}/files")).json()
    assert files and files[0]["path"] == "app.py"
    diff = (await client.get(f"/api/runs/{run_id}/diff", params={"path": "app.py", "version": 1})).text
    assert diff.startswith("--- a/app.py") and "+def main" in diff
    content = (await client.get(f"/api/runs/{run_id}/file", params={"path": "app.py"})).text
    assert "def main" in content
    md = (await client.get(f"/api/runs/{run_id}/export.md")).text
    assert md.startswith("# Hive run: write app.py") and "## Timeline" in md and "```diff" in md
    listed = (await client.get("/api/runs")).json()
    assert listed[0]["id"] == run_id and listed[0]["active"] is False


async def test_stream_replays_then_ends_quietly(client):
    r = await client.post("/api/runs", headers=H, json={"goal": "write a.py", "mode": "solo", "team": [{"role": "coder"}]})
    run_id = r.json()["id"]
    await wait_status(client, run_id)
    kinds = []
    async with client.stream("GET", f"/api/runs/{run_id}/stream") as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        event = "message"
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                if event == "message":
                    kinds.append(json.loads(line[5:])["kind"])
                if event == "synced":
                    break
                event = "message"
    assert kinds[0] == "run.status" and kinds[-1] == "run.finished"


async def test_controls_and_errors(client):
    r = await client.post("/api/runs", headers=H, json={"goal": "x", "mode": "nope"})
    assert r.status_code == 400
    r = await client.post("/api/runs", headers=H, json={"goal": "x", "team": [{"role": "wizard"}]})
    assert r.status_code == 400 and "unknown role" in r.json()["error"]
    r = await client.post("/api/runs/doesnotexist/pause", headers=H, json={})
    assert r.status_code == 409


async def test_rollback_over_the_api(client):
    hive = client.hive
    from hive.modes import MODES
    from hive.modes.base import Mode

    class Waiting(Mode):
        name = "waiting"
        waits_for_user = True

    MODES["waiting"] = Waiting
    try:
        r = await client.post("/api/runs", headers=H, json={"goal": "files", "mode": "waiting", "team": [{"role": "coder"}]})
        run_id = r.json()["id"]
        run = hive.runs[run_id]
        start_cp = (await client.get(f"/api/runs/{run_id}")).json()["checkpoints"][0]["id"]
        await client.post(f"/api/runs/{run_id}/message", headers=H, json={"to": "coder", "text": "write extra.py"})
        for _ in range(200):
            if (run.workspace.root / "extra.py").exists() and not run.agents["coder"].busy:
                break
            await asyncio.sleep(0.02)
        assert (run.workspace.root / "extra.py").exists()
        r = await client.post(f"/api/runs/{run_id}/rollback", headers=H, json={"checkpoint": start_cp})
        assert r.status_code == 200, r.text
        assert not (run.workspace.root / "extra.py").exists()
        assert run.agents["coder"].history == []
        events = (await client.get(f"/api/runs/{run_id}/events")).json()
        assert any(e["rolled_back"] for e in events)
        assert "checkpoint.restored" in [e["kind"] for e in events]
        tool_calls = [e for e in events if e["kind"] == "tool.call"]
        assert tool_calls and all(e["rolled_back"] for e in tool_calls)
        await client.post(f"/api/runs/{run_id}/stop", headers=H, json={})
    finally:
        MODES.pop("waiting", None)


async def test_resume_over_the_api(client):
    r = await client.post("/api/runs", headers=H, json={"goal": "write a.py", "mode": "pipeline",
                                                          "team": [{"role": "coder"}, {"role": "reviewer"}], "limits": {"max_turns_per_run": 1}})
    run_id = r.json()["id"]
    await wait_status(client, run_id, want=("budget_exhausted",))
    r = await client.post(f"/api/runs/{run_id}/resume-run", headers=H, json={"limits": {"max_turns_per_run": 40}})
    assert r.status_code == 200, r.text
    await wait_status(client, run_id)
    r = await client.post(f"/api/runs/{run_id}/resume-run", headers=H, json={})
    assert r.status_code == 400 and "finished successfully" in r.json()["error"]
