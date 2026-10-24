import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hive.config import BackendConfig, Timeouts  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch):
    """Never touch the real config or data dirs."""
    monkeypatch.setenv("HIVE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("HIVE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("OLLAMA_HOST", raising=False)


def mock_config(name="mock", **kw) -> BackendConfig:
    opts = kw.pop("options", {})
    return BackendConfig(name=name, type="mock", default_model=kw.pop("default_model", "echo"), options=opts, **kw)


def fast_timeouts(**kw) -> Timeouts:
    return Timeouts(**{"connect": 1, "first_token": 2, "idle": 2, "total": 5, **kw})
