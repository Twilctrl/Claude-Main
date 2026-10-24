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


DEFAULT_BACKENDS = """
backends:
  - name: mock
    type: mock
    default_model: fake
    concurrency: 4
    context: 8192
    tools: true
  - name: text
    type: mock
    default_model: fake
    concurrency: 4
    context: 4096
    tools: false
"""


@pytest.fixture
async def make_app(tmp_path):
    """Build a Hive app on an in-memory database: ``app = await make_app(backends_yaml=..., hive_yaml=...)``."""
    from hive.config import load_settings
    from hive.runtime.app import Hive

    apps = []

    async def make(backends_yaml: str = DEFAULT_BACKENDS, hive_yaml: str = "", tools: dict | None = None):
        cfg = tmp_path / f"cfg{len(apps)}"
        cfg.mkdir()
        (cfg / "backends.yaml").write_text(backends_yaml)
        if hive_yaml:
            (cfg / "hive.yaml").write_text(hive_yaml)
        for name, code in (tools or {}).items():
            (cfg / "tools").mkdir(exist_ok=True)
            (cfg / "tools" / name).write_text(code)
        app = await Hive.open(load_settings(cfg), db=":memory:", start_health=False)
        for b in app.scheduler.backends.values():
            b.latency = 0
        app.scheduler.backoff_base = 0.01
        apps.append(app)
        return app

    yield make
    for app in apps:
        await app.close()
