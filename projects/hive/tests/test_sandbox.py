"""Workspace path sandboxing."""

import os
import sys

import pytest

from hive.runtime.workspace import SandboxError, Workspace


@pytest.fixture
def ws(tmp_path):
    return Workspace(tmp_path / "ws")


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "../outside.txt",
        "a/../../outside.txt",
        "..",
        "C:/Windows/system.ini",
        "C:relative.txt",
        "\\\\server\\share\\x",
        "//server/share/x",
        "\\absolute",
        "~/secrets",
        "notes.txt:hidden",
        "CON",
        "nul.txt",
        "dir/COM1.log",
        "LPT9",
        "bad\x00name",
    ],
)
def test_rejected_paths(ws, path):
    with pytest.raises(SandboxError):
        ws.resolve(path)


@pytest.mark.parametrize(
    "path,norm",
    [
        ("a.txt", "a.txt"),
        ("./a.txt", "a.txt"),
        ("dir\\sub\\b.py", "dir/sub/b.py"),
        ("dir/../c.txt", "c.txt"),
        ("  spaced.md ", "spaced.md"),
        ("console.txt", "console.txt"),
        (".", "."),
        ("", "."),
    ],
)
def test_allowed_paths(ws, path, norm):
    assert ws.normalize(path) == norm
    assert str(ws.resolve(path)).startswith(str(ws.root))


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_symlink_escape_is_refused(ws, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    os.symlink(secret, ws.root / "link.txt")
    os.symlink(tmp_path, ws.root / "linkdir")
    with pytest.raises(SandboxError, match="outside"):
        ws.read_bytes("link.txt")
    with pytest.raises(SandboxError, match="outside"):
        ws.write_bytes("linkdir/new.txt", b"x")
    assert not (tmp_path / "new.txt").exists()
    files, _ = ws.list(".")
    assert "link.txt" not in [f.path for f in files]


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_symlink_inside_is_fine(ws):
    (ws.root / "real.txt").write_text("hi")
    os.symlink(ws.root / "real.txt", ws.root / "alias.txt")
    assert ws.read_bytes("alias.txt") == b"hi"


def test_git_dir_is_read_only(ws):
    (ws.root / ".git").mkdir()
    (ws.root / ".git" / "config").write_text("[core]")
    assert ws.read_bytes(".git/config") == b"[core]"
    with pytest.raises(SandboxError, match=".git"):
        ws.write_bytes(".git/hooks/pre-commit", b"#!/bin/sh\nrm -rf ~")


def test_write_is_atomic_and_creates_dirs(ws):
    ws.write_bytes("deep/dir/f.txt", b"one")
    ws.write_bytes("deep/dir/f.txt", b"two")
    assert (ws.root / "deep/dir/f.txt").read_bytes() == b"two"
    assert not any(p.name.startswith(".hive-tmp-") for p in (ws.root / "deep/dir").iterdir())


def test_size_limits(ws, monkeypatch):
    import hive.runtime.workspace as w

    monkeypatch.setattr(w, "MAX_WRITE_BYTES", 10)
    with pytest.raises(SandboxError, match="limit"):
        ws.write_bytes("big.txt", b"x" * 11)


def test_cannot_write_root_or_delete_dirs(ws):
    (ws.root / "d").mkdir()
    with pytest.raises(SandboxError):
        ws.write_bytes(".", b"x")
    with pytest.raises(SandboxError, match="folder"):
        ws.delete("d")
