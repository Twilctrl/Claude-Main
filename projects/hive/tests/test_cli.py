"""The CLI: init writes config that loads, and the main commands run on the mock backend."""

import socket

from hive.cli import main, pick_model
from hive.config import load_settings


def closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_init_writes_config_that_loads(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OLLAMA_HOST", f"127.0.0.1:{closed_port()}")
    cfg = tmp_path / "cfg"
    assert main(["--config", str(cfg), "init"]) == 0
    s = load_settings(cfg)  # the generated hive.yaml and backends.yaml must parse
    assert s.default_backend() == "mock" or s.hive.default_backend == "mock"
    assert s.hive.notify.events == ["finished", "approval", "attention"]
    assert (cfg / "tools").is_dir() and (cfg / "roles").is_dir()
    assert "no Ollama answering" in capsys.readouterr().out
    assert main(["--config", str(cfg), "init"]) == 0  # second time leaves files alone


def test_pick_model_prefers_chat_models():
    assert pick_model(["nomic-embed-text:latest", "gemma3:1b", "llama3.2:3b"]) == "llama3.2:3b"
    assert pick_model(["nomic-embed-text:latest"]) is None
    assert pick_model(["mystery:7b"]) == "mystery:7b"


def test_run_show_export_replay(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OLLAMA_HOST", f"127.0.0.1:{closed_port()}")
    cfg = tmp_path / "cfg"
    main(["--config", str(cfg), "init"])
    assert main(["--config", str(cfg), "run", "write hello.py", "-b", "mock", "--approve", "none", "-q"]) == 0
    out = capsys.readouterr().out
    assert "run done" in out and "hello.py" in out
    assert main(["--config", str(cfg), "runs"]) == 0
    run_id = capsys.readouterr().out.split()[0].replace("\x1b[1m", "").replace("\x1b[0m", "")
    assert main(["--config", str(cfg), "show", run_id]) == 0
    md = tmp_path / "run.md"
    assert main(["--config", str(cfg), "export", run_id, "-o", str(md)]) == 0
    assert md.read_text(encoding="utf-8").startswith("# Hive run: write hello.py")
    assert main(["--config", str(cfg), "replay", run_id, "--speed", "0"]) == 0
    assert "run done" in capsys.readouterr().out
    assert main(["--config", str(cfg), "roles"]) == 0
