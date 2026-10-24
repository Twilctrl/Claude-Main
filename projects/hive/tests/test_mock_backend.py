import json

import pytest

from hive.backends import BackendError, BackendUnavailable, ChatMessage, TextDelta, ToolSpec, create_backend

from conftest import mock_config

TOOLS = [ToolSpec("write_file", "Write a file")]


async def run(b, msgs=None, **kw):
    return [e async for e in b.chat(msgs or [ChatMessage("user", "hello there")], **kw)]


async def test_echo_streams_words():
    b = create_backend(mock_config())
    evs = await run(b, stream=True)
    assert "".join(e.text for e in evs if isinstance(e, TextDelta)) == "echo: hello there"
    assert evs[-1].text == "echo: hello there"


async def test_script_native_and_text_protocol():
    script = {"bot": [{"tool": "write_file", "args": {"path": "a"}}, "done"]}
    native = create_backend(mock_config(default_model="bot", tools=True, options={"script": script}))
    r1 = (await run(native, tools=TOOLS))[-1]
    assert r1.tool_calls[0].name == "write_file" and r1.text == ""
    assert (await run(native, tools=TOOLS))[-1].text == "done"
    assert (await run(native))[-1].text == "[script finished]"

    textual = create_backend(mock_config(default_model="bot", tools=False, options={"script": script}))
    r = (await run(textual, tools=TOOLS))[-1]
    assert r.tool_calls == [] and json.loads(r.text) == {"tool": "write_file", "args": {"path": "a"}}


async def test_failure_injection_and_down():
    b = create_backend(mock_config(options={"fail_calls": [2], "fail_status": 503}))
    await run(b)
    with pytest.raises(BackendError) as ei:
        await run(b)
    assert ei.value.retryable and ei.value.status == 503
    await run(b)
    b.down = True
    with pytest.raises(BackendUnavailable):
        await run(b)
    assert not (await b.health()).ok
