"""Optimistic versioning of shared files."""

import pytest

from hive.runtime.events import Event
from hive.runtime.files import ConflictError, FileTracker, version_diff
from hive.runtime.workspace import SandboxError, Workspace
from hive.store import Store


@pytest.fixture
async def tracker(tmp_path):
    store = await Store(":memory:").open()
    await store.add_project("p1", "p", "p", "goal", str(tmp_path / "ws"))
    await store.add_run("r1", "p1", "solo", "goal", {})
    events = []
    seq = 0

    async def emit(kind, agent=None, **payload):
        nonlocal seq
        seq += 1
        events.append((kind, agent, payload))
        return Event("r1", seq, 0, kind, agent, payload)

    t = FileTracker(store, "p1", "r1", Workspace(tmp_path / "ws"), emit)
    t.events = events
    yield t
    await store.close()


async def test_create_then_rewrite_own_file(tracker):
    info = await tracker.write("coder", "a.py", "print(1)\n")
    assert info["created"] and info["version"] == 1
    info = await tracker.write("coder", "a.py", "print(2)\n")
    assert info["version"] == 2
    assert tracker.ws.read_bytes("a.py") == b"print(2)\n"


async def test_existing_file_must_be_read_first(tracker):
    await tracker.write("coder", "a.py", "x = 1\n")
    with pytest.raises(ConflictError, match="haven't read it"):
        await tracker.write("writer", "a.py", "x = 2\n")
    await tracker.read("writer", "a.py")
    assert (await tracker.write("writer", "a.py", "x = 2\n"))["version"] == 2


async def test_stale_write_is_rejected_with_diff(tracker):
    await tracker.write("coder", "a.py", "x = 1\n")
    await tracker.read("reviewer", "a.py")
    await tracker.write("coder", "a.py", "x = 1\ny = 2\n")
    with pytest.raises(ConflictError) as ei:
        await tracker.write("reviewer", "a.py", "x = 100\n")
    msg = str(ei.value)
    assert "changed by coder" in msg and "+y = 2" in msg
    assert tracker.ws.read_bytes("a.py") == b"x = 1\ny = 2\n"


async def test_edit_merges_concurrent_changes(tracker):
    await tracker.write("coder", "a.py", "def f():\n    return 1\n\ndef g():\n    return 2\n")
    await tracker.edit("coder", "a.py", "return 1", "return 10")
    await tracker.edit("writer", "a.py", "return 2", "return 20")  # never read it; find still matches
    assert tracker.ws.read_bytes("a.py").decode() == "def f():\n    return 10\n\ndef g():\n    return 20\n"


async def test_edit_errors(tracker):
    await tracker.write("coder", "a.txt", "one\ntwo\ntwo\n")
    with pytest.raises(SandboxError, match="isn't in"):
        await tracker.edit("coder", "a.txt", "three", "3")
    with pytest.raises(SandboxError, match="2 times"):
        await tracker.edit("coder", "a.txt", "two", "2")
    info = await tracker.edit("coder", "a.txt", "two", "2", replace_all=True)
    assert info["replacements"] == 2
    with pytest.raises(SandboxError, match="no such file"):
        await tracker.edit("coder", "nope.txt", "a", "b")


async def test_external_edit_is_recorded(tracker):
    await tracker.write("coder", "a.txt", "mine\n")
    (tracker.ws.root / "a.txt").write_text("someone else\n")
    await tracker.read("coder", "a.txt")
    rows = await tracker.store.file_versions("p1", "a.txt")
    assert [r["agent"] for r in rows] == ["coder", "external"]


async def test_preexisting_file_is_baseline_without_event(tracker):
    (tracker.ws.root / "old.txt").write_text("already here\n")
    await tracker.read("coder", "old.txt")
    assert not [e for e in tracker.events if e[0] == "file.version"]
    rows = await tracker.store.file_versions("p1", "old.txt")
    assert rows[0]["agent"] == "external"


async def test_delete_and_diff(tracker):
    await tracker.write("coder", "a.txt", "one\n")
    await tracker.write("coder", "a.txt", "one\ntwo\n")
    diff = await version_diff(tracker.store, "p1", "a.txt", 2)
    assert "+two" in diff
    await tracker.delete("coder", "a.txt")
    assert not (tracker.ws.root / "a.txt").exists()
    ev = [p for k, _, p in tracker.events if k == "file.version"]
    assert ev[-1]["deleted"] and ev[1]["added"] == 1


async def test_scan_records_shell_changes(tracker):
    await tracker.write("coder", "keep.txt", "k\n")
    await tracker.write("coder", "gone.txt", "g\n")
    (tracker.ws.root / "new.txt").write_text("n\n")
    (tracker.ws.root / "gone.txt").unlink()
    changed = await tracker.scan("tester (shell)")
    assert changed == ["gone.txt", "new.txt"]
    assert await tracker.scan("x") == []


async def test_snapshot_and_restore(tracker):
    await tracker.write("coder", "a.txt", "v1\n")
    snap = await tracker.snapshot()
    await tracker.write("coder", "a.txt", "v2\n")
    await tracker.write("coder", "b.txt", "new\n")
    await tracker.restore(snap)
    assert tracker.ws.read_bytes("a.txt") == b"v1\n"
    assert not (tracker.ws.root / "b.txt").exists()
