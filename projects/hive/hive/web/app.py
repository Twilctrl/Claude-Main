"""The web UI's server: a JSON API for actions, SSE for live events, and the static app.

Security (DESIGN.md §13):

- Bound to localhost by default. Binding to anything else requires a token,
  sent as ``Authorization: Bearer``, the ``hive_token`` cookie, or ``?token=``
  (which sets the cookie).
- Every API request that changes something must carry ``X-Hive: 1``.
  Browsers can't add that header to cross-site requests without a CORS
  preflight, which this server never allows, so other websites can't drive
  your agents (CSRF). Reads don't need it: other sites can't see responses.
- On localhost, the Host header must be a loopback name, which blocks DNS
  rebinding.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import ipaddress
import json
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..config import ConfigError
from ..runtime.app import Hive
from ..runtime.board import BoardError
from ..runtime.events import Event
from ..runtime.files import version_diff
from ..runtime.run import Run

STATIC = Path(__file__).resolve().parent / "static"
LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1", "[::1]"}


def host_of(header: str) -> str:
    """The hostname part of a Host header: 'localhost:8765' -> 'localhost', '[::1]:80' -> '[::1]'."""
    if header.startswith("["):
        return header.split("]", 1)[0] + "]"
    return header.rsplit(":", 1)[0] if header.count(":") == 1 else header


def is_loopback(host: str) -> bool:
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


# --- request bodies ------------------------------------------------------------------------


class Member(BaseModel):
    role: str
    name: str | None = None
    backend: str | None = None
    model: str | None = None
    tools: list[str] | None = None
    system_prompt: str | None = None


class NewRun(BaseModel):
    goal: str = Field(min_length=1)
    mode: str = "pipeline"
    team: list[Member] = Field(default_factory=list)
    project: str | None = None
    workspace: str | None = None
    name: str | None = None
    limits: dict[str, Any] = Field(default_factory=dict)
    approval: str = "ask"


class AgentTarget(BaseModel):
    agent: str | None = None


class UserMessage(BaseModel):
    to: str
    text: str = Field(min_length=1)


class Decision(BaseModel):
    approved: bool
    reason: str = ""


class CheckpointReq(BaseModel):
    label: str = "manual"


class RollbackReq(BaseModel):
    checkpoint: int


# --- the app ------------------------------------------------------------------------------------


def create_app(hive: Hive, *, token: str | None = None, host: str = "127.0.0.1") -> FastAPI:
    app = FastAPI(title="Hive", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    loopback_only = is_loopback(host)

    def ok_token(request: Request) -> bool:
        if not token:
            return True
        given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        given = given or request.cookies.get("hive_token", "") or request.query_params.get("token", "")
        return hmac.compare_digest(given.encode(), token.encode())

    @app.middleware("http")
    async def guard(request: Request, call_next: Any) -> Response:
        if loopback_only and not is_loopback(host_of(request.headers.get("host", ""))):
            return PlainTextResponse("Hive only answers to localhost names when bound to localhost.", status_code=403)
        path = request.url.path
        if token and request.query_params.get("token") and path == "/":
            if not ok_token(request):
                return PlainTextResponse("wrong token", status_code=401)
            resp = RedirectResponse("/")
            resp.set_cookie("hive_token", token, httponly=True, samesite="strict")
            return resp
        if path.startswith("/api/") or path == "/":
            if not ok_token(request):
                if path == "/":
                    return PlainTextResponse("This Hive needs a token: open the URL printed by `hive serve`.", status_code=401)
                return JSONResponse({"error": "token required"}, status_code=401)
            # Reads are safe from other sites (they can't see the response); changes need the header.
            if path.startswith("/api/") and request.method != "GET" and request.headers.get("x-hive") != "1":
                return JSONResponse({"error": "missing X-Hive header"}, status_code=403)
        return await call_next(request)

    def get_run(run_id: str) -> Run:
        run = hive.runs.get(run_id)
        if run is None:
            raise HTTPException(409, "that run isn't active in this server (it has finished or was started elsewhere)")
        return run

    async def run_row(run_id: str) -> dict[str, Any]:
        row = await hive.store.get_run(run_id)
        if not row:
            raise HTTPException(404, "no such run")
        return row

    @app.exception_handler(BoardError)
    async def board_error(request: Request, exc: BoardError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(ConfigError)
    async def config_error(request: Request, exc: ConfigError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(ValueError)
    async def value_error(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=400)

    # Pages ------------------------------------------------------------------------------------------

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    # Info ---------------------------------------------------------------------------------------------

    @app.get("/api/info")
    async def info() -> dict[str, Any]:
        from ..modes import MODES
        from ..tools import all_tools

        return {
            "version": __version__,
            "modes": [{"name": n, "description": m.description, "default_team": m.default_team} for n, m in MODES.items()],
            "roles": [{"name": r.name, "description": r.description, "tools": r.tools, "system_prompt": r.system_prompt} for r in hive.roles().values()],
            "backends": hive.scheduler.snapshot(),
            "default_backend": hive.settings.default_backend(),
            "tools": [{"name": t.name, "group": t.group, "description": t.description, "risky": t.risky is not False} for t in all_tools().values()],
            "limits": hive.settings.hive.limits.model_dump(),
        }

    @app.get("/api/backends")
    async def backends() -> list[dict[str, Any]]:
        return hive.scheduler.snapshot()

    @app.post("/api/backends/check")
    async def check_backends() -> list[dict[str, Any]]:
        await hive.scheduler.check_health(force=True)
        return hive.scheduler.snapshot()

    @app.get("/api/backends/{name}/models")
    async def backend_models(name: str) -> list[str]:
        try:
            return [m.name for m in await hive.scheduler.backend(name).list_models()]
        except Exception as e:
            raise HTTPException(502, str(e)) from None

    # Projects and runs ------------------------------------------------------------------------------

    @app.get("/api/projects")
    async def projects() -> list[dict[str, Any]]:
        return await hive.store.list_projects()

    @app.get("/api/runs")
    async def runs(limit: int = 50) -> list[dict[str, Any]]:
        rows = await hive.store.list_runs(limit=limit)
        for r in rows:
            r["active"] = r["id"] in hive.runs and not hive.runs[r["id"]].done.is_set()
            r.pop("config", None)
        return rows

    @app.post("/api/runs")
    async def new_run(body: NewRun) -> dict[str, Any]:
        if body.approval not in ("ask", "all", "none"):
            raise HTTPException(400, "approval must be ask, all or none")
        if body.project:
            project = await hive.store.get_project(body.project)
            if not project:
                raise HTTPException(404, "no such project")
        else:
            project = await hive.create_project(body.goal, name=body.name, workspace=body.workspace)
        from ..modes import MODES

        if body.mode not in MODES:
            raise HTTPException(400, f"unknown mode {body.mode!r}")
        members: list[Any] = [m.model_dump(exclude_none=True) for m in body.team] or list(MODES[body.mode].default_team)
        team = hive.build_team(members)
        run = await hive.start_run(project, goal=body.goal, mode=body.mode, team=team, limits=body.limits or None, approval=body.approval)  # type: ignore[arg-type]
        return {"id": run.id, "project": project["slug"]}

    @app.get("/api/runs/{run_id}")
    async def run_detail(run_id: str) -> dict[str, Any]:
        row = await run_row(run_id)
        row["active"] = row["id"] in hive.runs and not hive.runs[row["id"]].done.is_set()
        row["project"] = await hive.store.get_project(row["project_id"])
        row["checkpoints"] = await hive.store.checkpoints(row["id"])
        row["usage"] = await hive.store.model_call_stats(row["id"])
        return row

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, after: int = 0) -> list[dict[str, Any]]:
        row = await run_row(run_id)
        return await hive.store.events(row["id"], after)

    @app.get("/api/runs/{run_id}/stream")
    async def run_stream(run_id: str, request: Request, after: int = 0) -> StreamingResponse:
        row = await run_row(run_id)
        rid = row["id"]
        try:  # a reconnecting EventSource says where it got to
            after = max(after, int(request.headers.get("last-event-id") or 0))
        except ValueError:
            pass

        async def gen() -> Any:
            sub = hive.bus.subscribe(rid)
            last = after
            last_load = 0.0
            try:
                for e in await hive.store.events(rid, after):
                    last = e["seq"]
                    yield _sse({"run_id": rid, **e}, e["seq"])
                yield "event: synced\ndata: {}\n\n"
                live = hive.runs.get(rid)
                if live is None or live.done.is_set():
                    # Finished (or run by another process): nothing more will come. The client
                    # closes on "end" rather than reconnecting.
                    yield "event: end\ndata: {}\n\n"
                    return
                while True:
                    if live.done.is_set() and sub.queue.empty():
                        yield "event: end\ndata: {}\n\n"
                        return
                    if await request.is_disconnected():
                        return
                    now = time.monotonic()
                    if now - last_load >= 1.0:
                        last_load = now
                        yield f"event: backends\ndata: {json.dumps(hive.scheduler.snapshot())}\n\n"
                    ev = await sub.get(timeout=1.0)
                    if sub.dropped:  # fell behind: catch up from the store
                        sub.dropped = False
                        for e in await hive.store.events(rid, last):
                            last = e["seq"]
                            yield _sse({"run_id": rid, **e}, e["seq"])
                    if ev is None:
                        continue
                    if ev.seq:
                        if ev.seq <= last:
                            continue
                        last = ev.seq
                    yield _sse(_event_dict(ev), ev.seq)
            finally:
                sub.close()

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # Controls -----------------------------------------------------------------------------------------

    @app.post("/api/runs/{run_id}/pause")
    async def pause(run_id: str, body: AgentTarget) -> dict[str, str]:
        await get_run(run_id).pause(body.agent)
        return {"ok": "paused"}

    @app.post("/api/runs/{run_id}/resume")
    async def resume(run_id: str, body: AgentTarget) -> dict[str, str]:
        await get_run(run_id).resume(body.agent)
        return {"ok": "resumed"}

    @app.post("/api/runs/{run_id}/interrupt")
    async def interrupt(run_id: str, body: AgentTarget) -> dict[str, Any]:
        run = get_run(run_id)
        names = [body.agent] if body.agent else list(run.agents)
        return {"interrupted": [n for n in names if run.agent(n).interrupt()]}

    @app.post("/api/runs/{run_id}/stop")
    async def stop(run_id: str) -> dict[str, str]:
        await get_run(run_id).stop()
        return {"ok": "stopped"}

    @app.post("/api/runs/{run_id}/message")
    async def message(run_id: str, body: UserMessage) -> dict[str, str]:
        await get_run(run_id).user_message(body.to, body.text)
        return {"ok": "sent"}

    @app.patch("/api/runs/{run_id}/agents/{name}")
    async def edit_agent(run_id: str, name: str, changes: dict[str, Any]) -> dict[str, Any]:
        spec = await get_run(run_id).update_agent(name, **changes)
        return spec.model_dump()

    @app.post("/api/runs/{run_id}/approvals/{approval_id}")
    async def decide(run_id: str, approval_id: int, body: Decision) -> dict[str, bool]:
        ok = await get_run(run_id).approvals.decide(approval_id, body.approved, "user", body.reason)
        if not ok:
            raise HTTPException(409, "that request is no longer waiting")
        return {"ok": True}

    @app.post("/api/runs/{run_id}/checkpoints")
    async def checkpoint(run_id: str, body: CheckpointReq) -> dict[str, int]:
        return {"id": await get_run(run_id).checkpoint(body.label or "manual")}

    @app.post("/api/runs/{run_id}/rollback")
    async def rollback(run_id: str, body: RollbackReq) -> dict[str, str]:
        await get_run(run_id).rollback(body.checkpoint)
        return {"ok": "rolled back"}

    # Files ---------------------------------------------------------------------------------------------

    @app.get("/api/runs/{run_id}/files")
    async def files(run_id: str) -> list[dict[str, Any]]:
        row = await run_row(run_id)
        return await hive.store.file_versions(row["project_id"], run_id=row["id"])

    @app.get("/api/runs/{run_id}/diff")
    async def diff(run_id: str, path: str, version: int) -> PlainTextResponse:
        row = await run_row(run_id)
        return PlainTextResponse(await version_diff(hive.store, row["project_id"], path, version))

    @app.get("/api/runs/{run_id}/file")
    async def file_content(run_id: str, path: str, version: int | None = None) -> PlainTextResponse:
        row = await run_row(run_id)
        rows = await hive.store.file_versions(row["project_id"], path)
        if version is not None:
            rows = [r for r in rows if r["version"] == version]
        if not rows or not rows[-1]["hash"]:
            raise HTTPException(404, "no such file version")
        data = await hive.store.get_blob(rows[-1]["hash"]) or b""
        return PlainTextResponse(data.decode("utf-8", "replace"))

    @app.get("/api/runs/{run_id}/export.md")
    async def export(run_id: str) -> PlainTextResponse:
        from ..export import transcript

        row = await run_row(run_id)
        md = await transcript(hive.store, row["id"])
        return PlainTextResponse(md, media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="hive-run-{row["id"]}.md"'})

    return app


def _event_dict(ev: Event) -> dict[str, Any]:
    return {"run_id": ev.run_id, "seq": ev.seq, "ts": ev.ts, "kind": ev.kind, "agent": ev.agent, "payload": ev.payload}


def _sse(data: dict[str, Any], seq: int) -> str:
    body = json.dumps(data, default=str, ensure_ascii=False)
    return (f"id: {seq}\n" if seq else "") + f"data: {body}\n\n"


async def serve(hive: Hive, host: str, port: int, token: str | None) -> None:
    """Run the server until cancelled."""
    import uvicorn

    app = create_app(hive, token=token, host=host)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", lifespan="off", access_log=False)
    server = uvicorn.Server(config)
    server.capture_signals = contextlib.nullcontext  # type: ignore[method-assign]  # Ctrl-C is ours
    task = asyncio.create_task(server.serve())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, 5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
        raise
