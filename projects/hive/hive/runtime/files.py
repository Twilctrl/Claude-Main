"""Versioned file access for agents: optimistic concurrency (DESIGN.md §8).

Every write is a new version (agent, time, content hash, blob in the store).
The tracker remembers which version each agent last read, so a small model
never has to handle version numbers:

- ``write`` to an existing file fails if the agent never read it, or if it
  changed since the agent read it. The error shows the diff so the agent can
  redo its change on the new content.
- ``edit`` (find/replace) applies whenever ``find`` still matches, so most
  concurrent edits merge on their own.
- Changes made outside Hive are noticed by hash and recorded as ``external``.
"""

from __future__ import annotations

import asyncio
import difflib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..store import Store
from .events import Event
from .workspace import SandboxError, Workspace, sha256

Emit = Callable[..., Awaitable[Event]]
_MISSING = object()


class ConflictError(SandboxError):
    """The file changed under the agent. The message tells it what to do."""


def is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def unified_diff(old: str, new: str, path: str, max_lines: int = 200) -> str:
    lines = list(
        difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), f"a/{path}", f"b/{path}", n=2)
    )
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... ({len(lines) - max_lines} more diff lines)\n"]
    return "".join(x if x.endswith("\n") else x + "\n" for x in lines)


def diffstat(old: str, new: str) -> tuple[int, int]:
    added = removed = 0
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


@dataclass
class Known:
    version: int
    hash: str | None  # None: deleted
    agent: str


class FileTracker:
    def __init__(self, store: Store, project_id: str, run_id: str, workspace: Workspace, emit: Emit):
        self.store = store
        self.project_id = project_id
        self.run_id = run_id
        self.ws = workspace
        self.emit = emit
        self.last_read: dict[tuple[str, str], str | None] = {}
        self.known: dict[str, Known] = {}
        self.changed: dict[str, str] = {}  # path -> last agent to change it in this run
        self._locks: dict[str, asyncio.Lock] = {}

    async def load(self) -> None:
        for path, row in (await self.store.latest_versions(self.project_id)).items():
            self.known[path] = Known(row["version"], None if row["deleted"] else row["hash"], row["agent"])

    def _lock(self, path: str) -> asyncio.Lock:
        return self._locks.setdefault(path, asyncio.Lock())

    async def _current(self, path: str) -> tuple[bytes | None, str | None]:
        abs_path = self.ws.resolve(path)
        if not abs_path.is_file():
            return None, None
        data = await asyncio.to_thread(self.ws.read_bytes, path)
        return data, sha256(data)

    async def _record(self, path: str, agent: str, data: bytes | None, *, old: bytes | None, emit: bool = True) -> int:
        """Store a new version of ``path`` (``data`` None = deleted) and announce it."""
        h = sha256(data) if data is not None else None
        if data is not None:
            await self.store.put_blob(h, data)  # type: ignore[arg-type]
        prev = self.known.get(path)
        version = (prev.version if prev else 0) + 1
        self.known[path] = Known(version, h, agent)
        if agent != "external":
            self.changed[path] = agent
        old_text = (old or b"").decode("utf-8", "replace")
        new_text = (data or b"").decode("utf-8", "replace")
        added, removed = (0, 0) if (old and is_binary(old)) or (data and is_binary(data)) else diffstat(old_text, new_text)
        seq = None
        if emit:
            ev = await self.emit(
                "file.version",
                agent,
                path=path,
                version=version,
                size=len(data or b""),
                deleted=data is None,
                created=prev is None or prev.hash is None,
                added=added,
                removed=removed,
                hash=h,
            )
            seq = ev.seq
        await self.store.add_file_version(
            self.project_id, self.run_id, path, version, agent, seq, h, len(data or b""), data is None
        )
        return version

    async def _observe(self, path: str, data: bytes | None, h: str | None) -> None:
        """Record a change made outside Hive (or a file Hive hasn't seen yet)."""
        k = self.known.get(path)
        known_hash = k.hash if k else None
        if h != known_hash:
            old = await self.store.get_blob(known_hash) if known_hash else None
            await self._record(path, "external", data, old=old, emit=k is not None)  # first sight: baseline, no event

    # Agent operations -------------------------------------------------------------------

    async def read(self, agent: str, path: str) -> bytes:
        path = self.ws.normalize(path)
        async with self._lock(path):
            data, h = await self._current(path)
            if data is None:
                self.last_read[(agent, path)] = None
                raise SandboxError(f"{path}: no such file")
            await self._observe(path, data, h)
            self.last_read[(agent, path)] = h
            return data

    async def write(self, agent: str, path: str, content: str) -> dict[str, Any]:
        path = self.ws.normalize(path)
        self.ws.resolve(path, for_write=True)
        async with self._lock(path):
            cur, cur_hash = await self._current(path)
            await self._observe(path, cur, cur_hash)
            if cur_hash is not None:
                seen = self.last_read.get((agent, path), _MISSING)
                if seen is _MISSING:
                    raise ConflictError(
                        f"{path} already exists and you haven't read it. Call read_file on it first, "
                        "or use edit_file to change part of it."
                    )
                if seen != cur_hash:
                    old = (await self.store.get_blob(seen) if isinstance(seen, str) else b"") or b""
                    who = self.known[path].agent
                    diff = unified_diff(old.decode("utf-8", "replace"), cur.decode("utf-8", "replace"), path)  # type: ignore[union-attr]
                    raise ConflictError(
                        f"{path} was changed by {who} after you read it, so your write was not applied. "
                        f"Their change:\n{diff}\nRead the file again and redo your change on the new version."
                    )
            new = content.encode("utf-8")
            new_hash = sha256(new)
            if new_hash == cur_hash:
                return {"path": path, "unchanged": True, "version": self.known[path].version}
            await asyncio.to_thread(self.ws.write_bytes, path, new)
            version = await self._record(path, agent, new, old=cur)
            self.last_read[(agent, path)] = new_hash
            return {"path": path, "version": version, "created": cur_hash is None, "bytes": len(new)}

    async def edit(self, agent: str, path: str, find: str, replace: str, replace_all: bool = False) -> dict[str, Any]:
        path = self.ws.normalize(path)
        self.ws.resolve(path, for_write=True)
        if not find:
            raise SandboxError("edit_file: 'find' must not be empty (use write_file to create a file)")
        async with self._lock(path):
            cur, cur_hash = await self._current(path)
            if cur is None:
                raise SandboxError(f"{path}: no such file (use write_file to create it)")
            await self._observe(path, cur, cur_hash)
            if is_binary(cur):
                raise SandboxError(f"{path} is a binary file")
            text = cur.decode("utf-8", "replace")
            count = text.count(find)
            if count == 0:
                # Small models often get whitespace wrong: retry ignoring trailing spaces per line.
                hint = _closest_line(text, find)
                raise SandboxError(
                    f"edit_file: the 'find' text isn't in {path}. Read the file again and copy the text exactly."
                    + (f" The closest line is: {hint!r}" if hint else "")
                )
            if count > 1 and not replace_all:
                raise SandboxError(
                    f"edit_file: the 'find' text appears {count} times in {path}. Include more surrounding text "
                    "so it matches once, or set replace_all to true."
                )
            new_text = text.replace(find, replace) if replace_all else text.replace(find, replace, 1)
            new = new_text.encode("utf-8")
            await asyncio.to_thread(self.ws.write_bytes, path, new)
            version = await self._record(path, agent, new, old=cur)
            if self.last_read.get((agent, path)) == cur_hash:
                self.last_read[(agent, path)] = sha256(new)  # still up to date
            return {"path": path, "version": version, "replacements": count if replace_all else 1}

    async def delete(self, agent: str, path: str) -> dict[str, Any]:
        path = self.ws.normalize(path)
        async with self._lock(path):
            cur, cur_hash = await self._current(path)
            if cur is None:
                raise SandboxError(f"{path}: no such file")
            await self._observe(path, cur, cur_hash)
            await asyncio.to_thread(self.ws.delete, path)
            version = await self._record(path, agent, None, old=cur)
            self.last_read.pop((agent, path), None)
            return {"path": path, "version": version, "deleted": True}

    async def restore(self, files: dict[str, str], agent: str = "rollback") -> None:
        """Make the workspace match ``files`` (path -> sha256), recording each change."""
        current = self.ws.all_files()
        for path in sorted(set(current) | set(files)):
            want, have = files.get(path), current.get(path)
            if want == have:
                continue
            async with self._lock(path):
                old = await asyncio.to_thread(self.ws.read_bytes, path) if have else None
                if want is None:
                    await asyncio.to_thread(self.ws.delete, path)
                    await self._record(path, agent, None, old=old)
                else:
                    data = await self.store.get_blob(want)
                    if data is None:
                        continue  # never stored; leave the file as it is
                    await asyncio.to_thread(self.ws.write_bytes, path, data)
                    await self._record(path, agent, data, old=old)
        self.last_read.clear()

    async def scan(self, agent: str) -> list[str]:
        """Record every file that changed on disk since Hive last looked (after a shell command)."""
        current = await asyncio.to_thread(self.ws.all_files)
        paths_ = set(current) | {p for p, k in self.known.items() if k.hash}
        changed = []
        for path in sorted(paths_):
            k = self.known.get(path)
            h = current.get(path)
            if (k.hash if k else None) == h:
                continue
            async with self._lock(path):
                data = await asyncio.to_thread(self.ws.read_bytes, path) if h else None
                old = await self.store.get_blob(k.hash) if k and k.hash else None
                await self._record(path, agent, data, old=old)
            changed.append(path)
        return changed

    async def snapshot(self) -> dict[str, str]:
        """path -> sha256 for every file, storing any blob not stored yet."""
        out: dict[str, str] = {}
        for path, h in self.ws.all_files().items():
            k = self.known.get(path)
            if not k or k.hash != h:
                data = await asyncio.to_thread(self.ws.read_bytes, path)
                await self._observe(path, data, h)
            out[path] = h
        return out


def _closest_line(text: str, find: str) -> str | None:
    first = find.strip().splitlines()[0].strip() if find.strip() else ""
    if not first:
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    match = difflib.get_close_matches(first, lines, n=1, cutoff=0.6)
    return match[0] if match else None


async def version_diff(store: Store, project_id: str, path: str, version: int) -> str:
    """The diff that ``version`` of ``path`` made against the version before it."""
    rows = await store.file_versions(project_id, path)
    by_v = {r["version"]: r for r in rows}
    cur = by_v.get(version)
    if cur is None:
        return ""
    prev = by_v.get(version - 1)

    async def text(row: dict[str, Any] | None) -> str:
        if not row or not row["hash"]:
            return ""
        data = await store.get_blob(row["hash"]) or b""
        return "(binary file)\n" if is_binary(data) else data.decode("utf-8", "replace")

    return unified_diff(await text(prev), await text(cur), path, max_lines=2000)


async def changes_since(store: Store, project_id: str, run_id: str, seq: int, max_chars: int = 6000) -> tuple[list[str], str]:
    """Files changed in ``run_id`` after event ``seq``, and one combined diff (clipped)."""
    rows = await store.file_versions(project_id)
    by_path: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_path.setdefault(r["path"], []).append(r)
    changed: list[str] = []
    parts: list[str] = []
    for path, versions in sorted(by_path.items()):
        after = [v for v in versions if v["run_id"] == run_id and (v["seq"] or 0) > seq and not v["agent"].startswith("external")]
        if not after:
            continue
        before = [v for v in versions if v not in after and (v["run_id"] != run_id or (v["seq"] or 0) <= seq)]
        old_row = before[-1] if before else None
        new_row = versions[-1]

        async def text(row: dict[str, Any] | None) -> str:
            if not row or not row["hash"]:
                return ""
            data = await store.get_blob(row["hash"]) or b""
            return "(binary file)\n" if is_binary(data) else data.decode("utf-8", "replace")

        changed.append(path)
        parts.append(unified_diff(await text(old_row), await text(new_row), path, max_lines=150))
    diff = "".join(parts)
    if len(diff) > max_chars:
        diff = diff[:max_chars] + f"\n[... diff cut at {max_chars} characters; read the files for the rest ...]\n"
    return changed, diff
