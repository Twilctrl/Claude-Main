"""The files in examples/ must keep working."""

import shutil
from pathlib import Path

import yaml

from hive.config import load_settings

EX = Path(__file__).resolve().parents[1] / "examples"


def test_backend_examples_load(tmp_path):
    for name in ("backends.single.yaml", "backends.lan.yaml"):
        d = tmp_path / name
        d.mkdir()
        shutil.copy(EX / name, d / "backends.yaml")
        s = load_settings(d)
        assert len(s.backends) >= 2  # plus the built-in mock
    lan = load_settings(tmp_path / "backends.lan.yaml")
    assert lan.backend("pi-npu").tools is False and lan.devices["desktop-gpu"].concurrency == 2
    assert [str(f) for f in lan.backend("pi-npu").fallbacks] == ["pi-cpu", "desktop"]


async def test_team_role_and_tool_examples_work_together(tmp_path):
    from hive.runtime.app import Hive

    cfg = tmp_path / "cfg"
    (cfg / "roles").mkdir(parents=True)
    (cfg / "tools").mkdir()
    shutil.copy(EX / "backends.lan.yaml", cfg / "backends.yaml")
    shutil.copy(EX / "roles" / "security-reviewer.yaml", cfg / "roles")
    shutil.copy(EX / "tools" / "word_count.py", cfg / "tools")
    app = await Hive.open(load_settings(cfg), db=":memory:", start_health=False)
    try:
        team_file = yaml.safe_load((EX / "team.mixed.yaml").read_text())
        team = app.build_team(team_file["team"])
        assert [a.backend for a in team] == ["desktop", "desktop", "desktop-llamacpp", "pi-cpu"]
        assert "run_shell" in team[3].tools and team[3].params == {"temperature": 0}
        assert "security-reviewer" in app.roles()
        from hive.tools import get_tool

        assert get_tool("word_count") is not None
    finally:
        await app.close()


async def test_word_count_plugin_in_a_run(make_app):
    code = (EX / "tools" / "word_count.py").read_text()
    script = """
backends:
  - name: mock
    type: mock
    default_model: counter
    tools: true
    options:
      script:
        counter:
          - {tool: write_file, args: {path: poem.txt, content: "one two three\\nfour five\\n"}}
          - {tool: word_count, args: {path: poem.txt}}
          - "counted"
"""
    app = await make_app(script, tools={"word_count.py": code})
    project = await app.create_project("count words")
    team = app.build_team([{"role": "writer", "tools": ["write_file", "word_count"]}])
    run = await app.start_run(project, mode="solo", team=team)
    assert await run.wait(10) == "done"
    results = [e["payload"]["output"] for e in await app.store.events(run.id) if e["kind"] == "tool.result"]
    assert "poem.txt: 2 lines, 5 words, 24 characters" in results
