"""The project workspace: a directory agents can touch only through these checks.

Every path an agent gives is relative to the workspace root. A path is refused
if it's absolute, has a drive letter or UNC prefix, uses a Windows reserved
device name or an NTFS stream (``:``), contains a NUL byte, or resolves
(symlinks followed) to anywhere outside the root. Writes under ``.git/`` are
refused too: a hook written there would run the next time you use git.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³]|conin\$|conout\$)(\..*)?$", re.IGNORECASE)
_PROTECTED_WRITE = (".git",)

MAX_READ_BYTES = 2_000_000
MAX_WRITE_BYTES = 2_000_000
MAX_LIST = 500
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache"}


class SandboxError(Exception):
    """A path or operation the sandbox refuses. The message is shown to the agent."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class FileInfo:
    path: str
    size: int
    is_dir: bool


class Workspace:
    """Sandboxed file access under ``root``."""

    def __init__(self, root: Path | str):
        self.root = Path(os.path.realpath(Path(root).expanduser()))
        self.root.mkdir(parents=True, exist_ok=True)
        self._root_cmp = os.path.normcase(str(self.root))

    # Path checks -------------------------------------------------------------------

    def normalize(self, rel: str) -> str:
        """Check ``rel`` and return it as a clean forward-slash relative path ('.' for the root)."""
        if not isinstance(rel, str):
            raise SandboxError("path must be a string")
        p = rel.strip().replace("\\", "/")
        if "\x00" in p:
            raise SandboxError("path contains a NUL byte")
        if p in ("", ".", "./"):
            return "."
        if p.startswith("~"):
            raise SandboxError(f"{rel!r}: paths are relative to the project folder; '~' isn't allowed")
        if p.startswith("/") or PureWindowsPath(rel).drive or PureWindowsPath(rel).root or PurePosixPath(p).is_absolute():
            raise SandboxError(f"{rel!r}: use a path relative to the project folder, not an absolute path")
        parts = []
        for part in PurePosixPath(p).parts:
            if part in ("", "."):
                continue
            if ":" in part:
                raise SandboxError(f"{rel!r}: ':' isn't allowed in paths")
            if _RESERVED.match(part.rstrip(" .")):
                raise SandboxError(f"{rel!r}: {part!r} is a reserved device name on Windows")
            if part == "..":
                if not parts:
                    raise SandboxError(f"{rel!r} is outside the project folder")
                parts.pop()
                continue
            parts.append(part)
        return "/".join(parts) or "."

    def resolve(self, rel: str, *, for_write: bool = False) -> Path:
        """The absolute path for ``rel``, after every check. Raises SandboxError."""
        norm = self.normalize(rel)
        if for_write:
            if norm == ".":
                raise SandboxError("can't write to the project folder itself")
            first = norm.split("/", 1)[0]
            if first in _PROTECTED_WRITE:
                raise SandboxError(f"{rel!r}: writing inside {first}/ isn't allowed")
        target = self.root / norm if norm != "." else self.root
        real = os.path.realpath(target)
        real_cmp = os.path.normcase(real)
        if real_cmp != self._root_cmp and not real_cmp.startswith(self._root_cmp.rstrip(os.sep) + os.sep):
            raise SandboxError(f"{rel!r} resolves outside the project folder")
        return Path(real)

    def rel(self, abs_path: Path) -> str:
        return Path(abs_path).relative_to(self.root).as_posix()

    # Operations ---------------------------------------------------------------------------

    def exists(self, rel: str) -> bool:
        return self.resolve(rel).exists()

    def read_bytes(self, rel: str) -> bytes:
        path = self.resolve(rel)
        if not path.exists():
            raise SandboxError(f"{self.normalize(rel)}: no such file")
        if path.is_dir():
            raise SandboxError(f"{self.normalize(rel)} is a folder; use list_files")
        size = path.stat().st_size
        if size > MAX_READ_BYTES:
            raise SandboxError(f"{self.normalize(rel)} is {size} bytes, over the {MAX_READ_BYTES}-byte read limit")
        return path.read_bytes()

    def current_hash(self, rel: str) -> str | None:
        path = self.resolve(rel)
        if not path.is_file():
            return None
        return sha256(path.read_bytes())

    def write_bytes(self, rel: str, data: bytes) -> Path:
        """Atomically replace (or create) a file: write a temp file beside it, then rename."""
        if len(data) > MAX_WRITE_BYTES:
            raise SandboxError(f"content is {len(data)} bytes, over the {MAX_WRITE_BYTES}-byte write limit")
        path = self.resolve(rel, for_write=True)
        if path.is_dir():
            raise SandboxError(f"{self.normalize(rel)} is a folder")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Re-check after creating parents, in case a parent was a symlink planted outside.
        self.resolve(rel, for_write=True)
        fd, tmp = tempfile.mkstemp(prefix=".hive-tmp-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return path

    def delete(self, rel: str) -> None:
        path = self.resolve(rel, for_write=True)
        if not path.exists():
            raise SandboxError(f"{self.normalize(rel)}: no such file")
        if path.is_dir():
            raise SandboxError(f"{self.normalize(rel)} is a folder; only files can be deleted")
        path.unlink()

    def list(self, rel: str = ".", recursive: bool = True, limit: int = MAX_LIST) -> tuple[list[FileInfo], bool]:
        """Files under ``rel``. Returns (entries, truncated)."""
        base = self.resolve(rel)
        if not base.exists():
            raise SandboxError(f"{self.normalize(rel)}: no such folder")
        if base.is_file():
            return [FileInfo(self.rel(base), base.stat().st_size, False)], False
        out: list[FileInfo] = []
        if recursive:
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
                for name in sorted(filenames):
                    if name.startswith(".hive-tmp-"):
                        continue
                    p = Path(dirpath) / name
                    try:
                        self.resolve(self.rel(p))
                    except (SandboxError, ValueError):
                        continue  # a symlink pointing outside: don't list it
                    out.append(FileInfo(self.rel(p), p.stat().st_size, False))
                    if len(out) >= limit:
                        return out, True
        else:
            for p in sorted(base.iterdir()):
                out.append(FileInfo(self.rel(p), p.stat().st_size if p.is_file() else 0, p.is_dir()))
                if len(out) >= limit:
                    return out, True
        return out, False

    def all_files(self) -> dict[str, str]:
        """Every file's sha256, for checkpoints."""
        files, _ = self.list(".", recursive=True, limit=100_000)
        return {f.path: sha256((self.root / f.path).read_bytes()) for f in files}
