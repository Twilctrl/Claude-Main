"""SQLite access on one dedicated thread, so a slow disk never blocks the event loop.

All methods are async. Calls run in order on the database thread, so writes
are serialized without locks.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import zlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")

SCHEMA = (Path(__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")
SCHEMA_VERSION = "1"


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


class Store:
    """The Hive database. ``path`` may be ``":memory:"`` for tests."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        self._ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hive-db")
        self._conn: sqlite3.Connection | None = None

    async def open(self) -> Store:
        def _open() -> None:
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path)
            conn.row_factory = sqlite3.Row
            conn.executescript(SCHEMA)
            conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema', ?)", (SCHEMA_VERSION,))
            conn.commit()
            self._conn = conn

        await self._run(_open)
        return self

    async def close(self) -> None:
        def _close() -> None:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

        await self._run(_close)
        self._ex.shutdown(wait=True)

    async def _run(self, fn: Callable[..., T], *args: Any) -> T:
        return await asyncio.get_running_loop().run_in_executor(self._ex, fn, *args)

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        """Run one write statement and commit. Returns lastrowid."""

        def go() -> int:
            assert self._conn is not None, "store not open"
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.lastrowid or 0

        return await self._run(go)

    async def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        def go() -> list[dict[str, Any]]:
            assert self._conn is not None, "store not open"
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

        return await self._run(go)

    async def query_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = await self.query(sql, params)
        return rows[0] if rows else None

    # Projects ---------------------------------------------------------------------

    async def add_project(self, id: str, slug: str, name: str, goal: str, workspace: str, settings: dict[str, Any] | None = None) -> None:
        await self.execute(
            "INSERT INTO projects(id, slug, name, goal, workspace, created_at, settings) VALUES (?,?,?,?,?,?,?)",
            (id, slug, name, goal, workspace, time.time(), json.dumps(settings or {})),
        )

    async def get_project(self, key: str) -> dict[str, Any] | None:
        return await self.query_one("SELECT * FROM projects WHERE id = ? OR slug = ?", (key, key))

    async def list_projects(self) -> list[dict[str, Any]]:
        return await self.query("SELECT * FROM projects ORDER BY created_at DESC")

    async def slug_taken(self, slug: str) -> bool:
        return await self.query_one("SELECT 1 FROM projects WHERE slug = ?", (slug,)) is not None

    async def delete_project(self, id: str) -> None:
        await self.execute("DELETE FROM projects WHERE id = ?", (id,))

    # Runs ----------------------------------------------------------------------------

    async def add_run(self, id: str, project_id: str, mode: str, goal: str, config: dict[str, Any]) -> None:
        await self.execute(
            "INSERT INTO runs(id, project_id, mode, goal, status, config, started_at) VALUES (?,?,?,?,?,?,?)",
            (id, project_id, mode, goal, "created", json.dumps(config), time.time()),
        )

    async def update_run(self, id: str, **fields: Any) -> None:
        if not fields:
            return
        cols, vals = [], []
        for k, v in fields.items():
            cols.append(f"{k} = ?")
            vals.append(json.dumps(v) if isinstance(v, (dict, list)) else v)
        await self.execute(f"UPDATE runs SET {', '.join(cols)} WHERE id = ?", (*vals, id))

    async def get_run(self, id: str) -> dict[str, Any] | None:
        row = await self.query_one(
            "SELECT * FROM runs WHERE id = ? OR id LIKE ? ORDER BY started_at DESC LIMIT 1", (id, f"{id}%")
        )
        if row:
            row["config"] = json.loads(row["config"])
            row["stats"] = json.loads(row["stats"] or "{}")
        return row

    async def list_runs(self, project_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT r.*, p.name AS project_name, p.slug AS project_slug FROM runs r JOIN projects p ON p.id = r.project_id"
        params: tuple[Any, ...] = ()
        if project_id:
            sql += " WHERE r.project_id = ?"
            params = (project_id,)
        sql += " ORDER BY r.started_at DESC LIMIT ?"
        rows = await self.query(sql, (*params, limit))
        for r in rows:
            r["config"] = json.loads(r["config"])
            r["stats"] = json.loads(r["stats"] or "{}")
        return rows

    # Agents ----------------------------------------------------------------------------

    async def upsert_agent(self, run_id: str, name: str, role: str, spec: dict[str, Any], status: str, state: dict[str, Any] | None = None) -> None:
        await self.execute(
            "INSERT INTO agents(run_id, name, role, spec, status, state) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(run_id, name) DO UPDATE SET role=excluded.role, spec=excluded.spec, status=excluded.status, state=excluded.state",
            (run_id, name, role, json.dumps(spec), status, json.dumps(state or {})),
        )

    async def get_agents(self, run_id: str) -> list[dict[str, Any]]:
        rows = await self.query("SELECT * FROM agents WHERE run_id = ? ORDER BY rowid", (run_id,))
        for r in rows:
            r["spec"] = json.loads(r["spec"])
            r["state"] = json.loads(r["state"] or "{}")
        return rows

    # Events --------------------------------------------------------------------------------

    async def append_event(self, run_id: str, seq: int, ts: float, kind: str, agent: str | None, payload: dict[str, Any]) -> None:
        await self.execute(
            "INSERT INTO events(run_id, seq, ts, kind, agent, payload) VALUES (?,?,?,?,?,?)",
            (run_id, seq, ts, kind, agent, json.dumps(payload, default=str)),
        )

    async def events(self, run_id: str, after: int = 0, limit: int | None = None, kinds: list[str] | None = None) -> list[dict[str, Any]]:
        sql = "SELECT seq, ts, kind, agent, payload, rolled_back FROM events WHERE run_id = ? AND seq > ?"
        params: list[Any] = [run_id, after]
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params += kinds
        sql += " ORDER BY seq"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        rows = await self.query(sql, tuple(params))
        for r in rows:
            r["payload"] = json.loads(r["payload"])
            r["rolled_back"] = bool(r["rolled_back"])
        return rows

    async def max_seq(self, run_id: str) -> int:
        row = await self.query_one("SELECT MAX(seq) AS m FROM events WHERE run_id = ?", (run_id,))
        return int(row["m"] or 0) if row else 0

    async def mark_rolled_back(self, run_id: str, after_seq: int, upto_seq: int) -> None:
        await self.execute(
            "UPDATE events SET rolled_back = 1 WHERE run_id = ? AND seq > ? AND seq <= ?", (run_id, after_seq, upto_seq)
        )

    # Messages, tasks, board -------------------------------------------------------------------

    async def add_message(self, run_id: str, seq: int, sender: str, recipient: str, content: str, depth: int) -> None:
        await self.execute(
            "INSERT INTO messages(run_id, seq, ts, sender, recipient, content, depth) VALUES (?,?,?,?,?,?,?)",
            (run_id, seq, time.time(), sender, recipient, content, depth),
        )

    async def messages(self, run_id: str) -> list[dict[str, Any]]:
        return await self.query("SELECT * FROM messages WHERE run_id = ? ORDER BY seq", (run_id,))

    async def upsert_task(self, run_id: str, id: int, data: dict[str, Any]) -> None:
        await self.execute(
            "INSERT INTO tasks(run_id, id, data) VALUES (?,?,?) ON CONFLICT(run_id, id) DO UPDATE SET data=excluded.data",
            (run_id, id, json.dumps(data)),
        )

    async def tasks(self, run_id: str) -> list[dict[str, Any]]:
        return [json.loads(r["data"]) for r in await self.query("SELECT data FROM tasks WHERE run_id = ? ORDER BY id", (run_id,))]

    async def replace_tasks(self, run_id: str, tasks: list[dict[str, Any]]) -> None:
        def go() -> None:
            assert self._conn is not None
            self._conn.execute("DELETE FROM tasks WHERE run_id = ?", (run_id,))
            self._conn.executemany(
                "INSERT INTO tasks(run_id, id, data) VALUES (?,?,?)", [(run_id, t["id"], json.dumps(t)) for t in tasks]
            )
            self._conn.commit()

        await self._run(go)

    async def upsert_board(self, run_id: str, id: int, data: dict[str, Any]) -> None:
        await self.execute(
            "INSERT INTO board(run_id, id, data) VALUES (?,?,?) ON CONFLICT(run_id, id) DO UPDATE SET data=excluded.data",
            (run_id, id, json.dumps(data)),
        )

    async def board(self, run_id: str) -> list[dict[str, Any]]:
        return [json.loads(r["data"]) for r in await self.query("SELECT data FROM board WHERE run_id = ? ORDER BY id", (run_id,))]

    async def replace_board(self, run_id: str, entries: list[dict[str, Any]]) -> None:
        def go() -> None:
            assert self._conn is not None
            self._conn.execute("DELETE FROM board WHERE run_id = ?", (run_id,))
            self._conn.executemany(
                "INSERT INTO board(run_id, id, data) VALUES (?,?,?)", [(run_id, e["id"], json.dumps(e)) for e in entries]
            )
            self._conn.commit()

        await self._run(go)

    # Blobs and file versions ----------------------------------------------------------------------

    async def put_blob(self, hash: str, content: bytes) -> None:
        await self.execute("INSERT OR IGNORE INTO blobs(hash, data) VALUES (?, ?)", (hash, zlib.compress(content, 6)))

    async def get_blob(self, hash: str) -> bytes | None:
        row = await self.query_one("SELECT data FROM blobs WHERE hash = ?", (hash,))
        return zlib.decompress(row["data"]) if row else None

    async def add_file_version(
        self, project_id: str, run_id: str | None, path: str, version: int, agent: str, seq: int | None,
        hash: str | None, size: int, deleted: bool,
    ) -> int:
        return await self.execute(
            "INSERT INTO file_versions(project_id, run_id, path, version, agent, seq, ts, hash, size, deleted) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (project_id, run_id, path, version, agent, seq, time.time(), hash, size, int(deleted)),
        )

    async def file_versions(self, project_id: str, path: str | None = None, run_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM file_versions WHERE project_id = ?"
        params: list[Any] = [project_id]
        if path is not None:
            sql += " AND path = ?"
            params.append(path)
        if run_id is not None:
            sql += " AND run_id = ?"
            params.append(run_id)
        return await self.query(sql + " ORDER BY id", tuple(params))

    async def latest_versions(self, project_id: str) -> dict[str, dict[str, Any]]:
        rows = await self.query(
            "SELECT fv.* FROM file_versions fv JOIN (SELECT path, MAX(version) AS v FROM file_versions WHERE project_id = ? GROUP BY path) m "
            "ON fv.path = m.path AND fv.version = m.v WHERE fv.project_id = ?",
            (project_id, project_id),
        )
        return {r["path"]: r for r in rows}

    async def get_file_version(self, id: int) -> dict[str, Any] | None:
        return await self.query_one("SELECT * FROM file_versions WHERE id = ?", (id,))

    # Model calls, approvals, checkpoints -------------------------------------------------------------

    async def add_model_call(self, run_id: str, agent: str, backend: str, model: str, prompt_tokens: int | None,
                             completion_tokens: int | None, latency_ms: float | None, ok: bool, error: str | None,
                             request: str | None = None, response: str | None = None) -> None:
        await self.execute(
            "INSERT INTO model_calls(run_id, ts, agent, backend, model, prompt_tokens, completion_tokens, latency_ms, ok, error, request, response) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, time.time(), agent, backend, model, prompt_tokens, completion_tokens, latency_ms, int(ok), error, request, response),
        )

    async def model_call_stats(self, run_id: str) -> list[dict[str, Any]]:
        return await self.query(
            "SELECT agent, backend, model, COUNT(*) AS calls, SUM(ok) AS ok, SUM(prompt_tokens) AS prompt_tokens, "
            "SUM(completion_tokens) AS completion_tokens, AVG(latency_ms) AS avg_ms FROM model_calls WHERE run_id = ? "
            "GROUP BY agent, backend, model",
            (run_id,),
        )

    async def add_approval(self, run_id: str, agent: str, tool: str, args: dict[str, Any]) -> int:
        return await self.execute(
            "INSERT INTO approvals(run_id, agent, tool, args, status, created_at) VALUES (?,?,?,?,?,?)",
            (run_id, agent, tool, json.dumps(args), "pending", time.time()),
        )

    async def decide_approval(self, id: int, status: str, decided_by: str, reason: str | None) -> None:
        await self.execute(
            "UPDATE approvals SET status = ?, decided_by = ?, reason = ?, decided_at = ? WHERE id = ?",
            (status, decided_by, reason, time.time(), id),
        )

    async def add_checkpoint(self, run_id: str, seq: int, label: str, state: dict[str, Any]) -> int:
        return await self.execute(
            "INSERT INTO checkpoints(run_id, seq, ts, label, state) VALUES (?,?,?,?,?)",
            (run_id, seq, time.time(), label, json.dumps(state)),
        )

    async def checkpoints(self, run_id: str) -> list[dict[str, Any]]:
        return await self.query("SELECT id, run_id, seq, ts, label FROM checkpoints WHERE run_id = ? ORDER BY id", (run_id,))

    async def get_checkpoint(self, id: int) -> dict[str, Any] | None:
        row = await self.query_one("SELECT * FROM checkpoints WHERE id = ?", (id,))
        if row:
            row["state"] = json.loads(row["state"])
        return row
