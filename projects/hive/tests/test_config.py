import pytest

from hive.config import ConfigError, load_settings, ollama_default_url


def test_defaults_without_files(tmp_path):
    s = load_settings(tmp_path)
    names = [b.name for b in s.backends]
    assert names == ["local", "mock"]
    assert s.default_backend() == "local"
    assert s.hive.limits.max_turns_per_agent == 30


def test_backends_yaml(tmp_path):
    (tmp_path / "backends.yaml").write_text(
        """
backends:
  - name: desktop
    type: ollama
    url: http://10.0.0.5:11434/
    default_model: qwen2.5:7b
    concurrency: 2
    device: gpu
    fallbacks: [pi, {backend: pi, model: gemma3:1b}]
    models:
      qwen2.5:7b: {context: 16384}
  - name: pi
    type: ollama
    url: http://pi5.local:8000
    tools: false
devices:
  gpu: {concurrency: 3}
"""
    )
    s = load_settings(tmp_path)
    d = s.backend("desktop")
    assert d.url == "http://10.0.0.5:11434"
    assert [str(f) for f in d.fallbacks] == ["pi", "pi/gemma3:1b"]
    assert s.devices["gpu"].concurrency == 3
    assert s.backend("pi").tools is False
    assert "mock" in [b.name for b in s.backends]


@pytest.mark.parametrize(
    "yaml_text,msg",
    [
        ("backends:\n  - {name: a}\n  - {name: a}\n", "two backends"),
        ("backends:\n  - {name: a, fallbacks: [nope]}\n", "isn't defined"),
        ("backends:\n  - {name: a, bogus: 1}\n", "bogus"),
        ("backends: [\n", "YAML"),
    ],
)
def test_bad_backends(tmp_path, yaml_text, msg):
    (tmp_path / "backends.yaml").write_text(yaml_text)
    with pytest.raises(ConfigError, match=msg):
        load_settings(tmp_path)


@pytest.mark.parametrize(
    "env,expected",
    [
        (None, "http://127.0.0.1:11434"),
        ("0.0.0.0", "http://127.0.0.1:11434"),
        ("192.168.1.9:9999", "http://192.168.1.9:9999"),
        ("https://box.lan/", "https://box.lan:11434"),
    ],
)
def test_ollama_host(monkeypatch, env, expected):
    if env is None:
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
    else:
        monkeypatch.setenv("OLLAMA_HOST", env)
    assert ollama_default_url() == expected
