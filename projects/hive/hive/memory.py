"""Agent memory: long-term notes an agent keeps across runs, with simple retrieval.

Each agent has a namespace, its name by default (set ``memory`` on the agent
to share a namespace or to switch memory off). A note records which project
it came from. Notes with no project (ones you add by hand) apply everywhere.

Notes are written three ways:

- the agent calls ``remember(text)``;
- at the end of a run each agent that worked writes up to three lessons for
  next time (``memory.reflect``, on by default);
- you add them with ``hive memory add`` or on the Memory page.

At the start of every turn the agent's most relevant notes (BM25 over the
turn's trigger, the goal and the current task; notes from the same project
rank higher) are pinned in its system prompt. Agents can also search
explicitly with ``recall(query)``.

Retrieval is plain Python over the agent's notes, which are capped
(``memory.max_per_agent``), so there's no dependency on SQLite's FTS5.
"""

from __future__ import annotations

import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .store import Store

_WORD = re.compile(r"[a-z0-9_]+(?:\.[a-z0-9]+)?")
_STOP = frozenset(
    "a an and are as at be but by can do for from has have how i if in into is it its of on or so that the their them "
    "then there these they this to was we were what when which will with you your all any should would "
    "could just also more most some than too very only own same about after again before here once out over under "
    "up down off one two".split()
)

def same_note(a: str, b: str) -> bool:
    """Two notes say the same thing: the same meaningful words, ignoring case, order and punctuation.
    (Plain text similarity would merge "m1.py ..." with "m2.py ...", which are different facts.)"""
    ta, tb = set(tokens(a)), set(tokens(b))
    return bool(ta) and ta == tb


def _stem(w: str) -> str:
    for suf in ("ing", "edly", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def tokens(text: str) -> list[str]:
    """Lower-cased, stop-word-free, lightly stemmed words. File names stay whole (``app.py``)."""
    return [_stem(w) for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1]


@dataclass
class Memory:
    id: int
    agent: str
    text: str
    kind: str
    source: str
    project_id: str | None
    run_id: str | None
    created_at: float
    uses: int = 0
    last_used_at: float | None = None
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def rank(query: str, notes: list[Memory], *, project_id: str | None = None, k1: float = 1.2, b: float = 0.75) -> list[Memory]:
    """Notes scored against ``query`` with BM25, best first; notes sharing no word are dropped.
    Same-project notes get a 1.5x boost and global notes (no project) 1.2x."""
    q = Counter(tokens(query))
    if not q or not notes:
        return []
    docs = [tokens(n.text) for n in notes]
    n_docs = len(docs)
    avg = sum(len(d) for d in docs) / n_docs or 1.0
    df: Counter[str] = Counter()
    for d in docs:
        df.update(set(d))
    out = []
    for note, d in zip(notes, docs, strict=True):
        tf = Counter(d)
        score = 0.0
        for term in q:
            if term not in tf:
                continue
            idf = math.log(1 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
            f = tf[term]
            score += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(d) / avg))
        if score <= 0:
            continue
        if note.project_id is None:
            score *= 1.2
        elif project_id and note.project_id == project_id:
            score *= 1.5
        score *= 1 + min(note.uses, 10) * 0.02  # notes that keep being useful edge ahead
        note.score = round(score, 3)
        out.append(note)
    out.sort(key=lambda n: (-n.score, -n.created_at))
    return out


class MemoryStore:
    """Agent memories in the Hive database."""

    def __init__(self, store: Store, max_per_agent: int = 300):
        self.store = store
        self.max_per_agent = max_per_agent

    @staticmethod
    def _row(r: dict[str, Any]) -> Memory:
        return Memory(r["id"], r["agent"], r["text"], r["kind"], r["source"], r["project_id"], r["run_id"],
                      r["created_at"], r["uses"], r["last_used_at"])

    async def notes(self, agent: str | None = None, project_id: str | None = None) -> list[Memory]:
        sql, params = "SELECT * FROM memories WHERE 1=1", []
        if agent:
            sql += " AND agent = ?"
            params.append(agent)
        if project_id:
            sql += " AND (project_id = ? OR project_id IS NULL)"
            params.append(project_id)
        return [self._row(r) for r in await self.store.query(sql + " ORDER BY id", tuple(params))]

    async def add(
        self, agent: str, text: str, *, kind: str = "note", source: str = "agent",
        project_id: str | None = None, run_id: str | None = None,
    ) -> tuple[Memory, bool]:
        """Save a note. A repeat of an existing note (see :func:`same_note`) in the same namespace and
        project isn't stored twice: the existing note is returned and counts as used. Returns (note, created)."""
        text = " ".join(text.split())[:600]
        if not text:
            raise ValueError("a memory needs some text")
        for m in await self.notes(agent):
            if m.project_id == project_id and same_note(text, m.text):
                await self.touch([m.id])
                return m, False
        now = time.time()
        mid = await self.store.execute(
            "INSERT INTO memories(agent, project_id, run_id, kind, text, source, created_at, uses) VALUES (?,?,?,?,?,?,?,0)",
            (agent, project_id, run_id, kind, text, source, now),
        )
        await self._prune(agent)
        return Memory(mid, agent, text, kind, source, project_id, run_id, now), True

    async def _prune(self, agent: str) -> None:
        """Keep each namespace under the cap: drop the least used, then the oldest. Notes you added stay."""
        rows = await self.store.query(
            "SELECT id FROM memories WHERE agent = ? AND source != 'user' ORDER BY uses ASC, COALESCE(last_used_at, created_at) ASC",
            (agent,),
        )
        total = (await self.store.query_one("SELECT COUNT(*) AS n FROM memories WHERE agent = ?", (agent,)) or {"n": 0})["n"]
        extra = total - self.max_per_agent
        for r in rows[: max(0, extra)]:
            await self.store.execute("DELETE FROM memories WHERE id = ?", (r["id"],))

    async def search(self, agent: str, query: str, *, project_id: str | None = None, limit: int = 4) -> list[Memory]:
        """The ``limit`` best notes for ``query`` from this namespace (this project's and global ones)."""
        return rank(query, await self.notes(agent, project_id), project_id=project_id)[:limit]

    async def touch(self, ids: list[int]) -> None:
        now = time.time()
        for i in ids:
            await self.store.execute("UPDATE memories SET uses = uses + 1, last_used_at = ? WHERE id = ?", (now, i))

    async def get(self, id: int) -> Memory | None:
        r = await self.store.query_one("SELECT * FROM memories WHERE id = ?", (id,))
        return self._row(r) if r else None

    async def delete(self, id: int) -> bool:
        exists = await self.get(id) is not None
        await self.store.execute("DELETE FROM memories WHERE id = ?", (id,))
        return exists

    async def clear(self, agent: str) -> int:
        n = len(await self.notes(agent))
        await self.store.execute("DELETE FROM memories WHERE agent = ?", (agent,))
        return n

    async def forget_run_after(self, run_id: str, ts: float) -> int:
        """Delete notes a run saved after ``ts`` (used by rollback)."""
        rows = await self.store.query("SELECT id FROM memories WHERE run_id = ? AND created_at > ?", (run_id, ts))
        for r in rows:
            await self.store.execute("DELETE FROM memories WHERE id = ?", (r["id"],))
        return len(rows)

    async def namespaces(self) -> list[dict[str, Any]]:
        return await self.store.query("SELECT agent, COUNT(*) AS notes, MAX(created_at) AS latest FROM memories GROUP BY agent ORDER BY agent")


REFLECT_PROMPT = """The run you were part of has ended.
Goal: {goal}
Outcome: {outcome}

Write up to 3 short lessons that would help you do better next time on this project or similar work:
facts about the project (file names, conventions, commands that work), mistakes to avoid, what the user wanted.
One per line, each starting with "- ". If there's nothing worth keeping, reply with just "- none"."""


def parse_lessons(text: str, limit: int = 3) -> list[str]:
    """Lines starting with "-", "*" or "1." from a reflection reply, minus empties and "none"."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)\s*$", line)
        if not m:
            continue
        lesson = m.group(1).strip().strip('"')
        if lesson.lower().rstrip(".") in ("none", "nothing", "n/a", "no lessons"):
            continue
        if len(lesson) >= 8:
            out.append(lesson[:300])
        if len(out) >= limit:
            break
    return out
