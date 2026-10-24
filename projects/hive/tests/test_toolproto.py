"""The JSON-in-text tool protocol for models without native tools."""

import pytest

from hive.toolproto import extract_json, parse_reply, tools_prompt

TOOLS = {"write_file", "read_file", "approve"}


@pytest.mark.parametrize(
    "text,tool,args",
    [
        ('{"tool": "read_file", "args": {"path": "a.txt"}}', "read_file", {"path": "a.txt"}),
        ('```json\n{"tool": "read_file", "args": {"path": "a.txt"}}\n```', "read_file", {"path": "a.txt"}),
        ('Sure! I will read it.\n{"tool": "read_file", "args": {"path": "a.txt"}} Done.', "read_file", {"path": "a.txt"}),
        ('{"tool": "read_file", "args": {"path": "a.txt",},}', "read_file", {"path": "a.txt"}),
        ("{'tool': 'read_file', 'args': {'path': 'a.txt'}}", "read_file", {"path": "a.txt"}),
        ('{"name": "read_file", "arguments": {"path": "a.txt"}}', "read_file", {"path": "a.txt"}),
        ('{"function": {"name": "read_file", "arguments": "{\\"path\\": \\"a.txt\\"}"}}', "read_file", {"path": "a.txt"}),
        ('{"tool": "read_file", "path": "a.txt"}', "read_file", {"path": "a.txt"}),
        ('{"action": "approve", "args": {"notes": "ok, it\'s {fine}"}}', "approve", {"notes": "ok, it's {fine}"}),
    ],
)
def test_tool_calls(text, tool, args):
    p = parse_reply(text, TOOLS)
    assert p.kind == "tool" and p.tool == tool and p.args == args


def test_code_with_braces_inside_strings():
    content = 'def f():\n    return {"a": 1}\n'
    import json

    text = json.dumps({"tool": "write_file", "args": {"path": "f.py", "content": content}})
    p = parse_reply("Here you go: " + text, TOOLS)
    assert p.kind == "tool" and p.args["content"] == content


@pytest.mark.parametrize(
    "text,final",
    [
        ('{"final": "all done"}', "all done"),
        ('{"answer": "42"}', "42"),
        ('```\n{"final": "ok"}\n```', "ok"),
    ],
)
def test_final(text, final):
    p = parse_reply(text, TOOLS)
    assert p.kind == "final" and p.text == final


def test_plain_text_is_a_final_answer():
    p = parse_reply("I think the answer is 42.", TOOLS)
    assert p.kind == "text" and p.text == "I think the answer is 42."


@pytest.mark.parametrize(
    "text,err",
    [
        ('{"tool": "rm_rf", "args": {}}', "no tool named"),
        ('{"tool": "read_file", "args": "nope"}', "must be a JSON object"),
        ('{"tool": "read_file", "args": {"path": "a.txt"', "isn't valid JSON"),
        ('{"thoughts": "hmm"}', 'no "tool" or "final"'),
    ],
)
def test_errors(text, err):
    p = parse_reply(text, TOOLS)
    assert p.kind == "error" and err in p.error


def test_extract_json_none():
    assert extract_json("no json here") is None


def test_prompt_lists_signatures():
    import hive.tools.builtin  # noqa: F401
    from hive.tools import get_tool

    text = tools_prompt([get_tool("read_file"), get_tool("write_file")])
    assert "read_file(path: string, start_line?: int, max_lines?: int)" in text
    assert '{"final":' in text
