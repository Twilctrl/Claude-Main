"""Tool schemas from type hints, validation, and plugin loading."""

import pytest

from hive.tools import ToolError, make_tool
from hive.tools.plugins import load_extra_tools, errors


async def sample(ctx, path: str, count: int = 3, tags: list[str] = [], flag: bool = False) -> str:  # noqa: B006
    """Do a sample thing.

    More detail that isn't the summary.

    Args:
        path: where to do it
        count: how many times
    """
    return "ok"


def test_schema_from_hints_and_docstring():
    t = make_tool(sample)
    p = t.parameters
    assert t.description == "Do a sample thing."
    assert p["required"] == ["path"]
    assert p["properties"]["path"] == {"type": "string", "description": "where to do it"}
    assert p["properties"]["count"]["type"] == "integer" and p["properties"]["count"]["default"] == 3
    assert p["properties"]["tags"]["items"] == {"type": "string"}
    assert "title" not in str(p)
    assert t.signature() == "sample(path: string, count?: int, tags?: list[string], flag?: bool)"


def test_validation_coerces_and_reports():
    t = make_tool(sample)
    assert t.validate({"path": "a", "count": "5", "extra": 1}) == {"path": "a", "count": 5, "tags": [], "flag": False}
    with pytest.raises(ToolError, match="'path' is required"):
        t.validate({})
    with pytest.raises(ToolError, match="count"):
        t.validate({"path": "a", "count": "lots"})


def test_bad_tool_definitions():
    def not_async(ctx):
        pass

    async def no_ctx(path: str):
        pass

    with pytest.raises(TypeError):
        make_tool(not_async)
    with pytest.raises(TypeError):
        make_tool(no_ctx)


def test_plugins_load_from_config_dir_only(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "wordcount.py").write_text(
        "from hive.tools import tool\n\n"
        "@tool()\n"
        "async def word_count(ctx, path: str) -> str:\n"
        '    """Count words in a file."""\n'
        "    return str(len(ctx.run.workspace.read_bytes(path).split()))\n"
    )
    (tools / "broken.py").write_text("raise RuntimeError('nope')\n")
    (tools / "_private.py").write_text("raise RuntimeError('should not load')\n")
    names = load_extra_tools(tmp_path)
    assert "word_count" in names
    assert any("broken.py" in k for k in errors)
    assert not any("_private" in k for k in errors)
