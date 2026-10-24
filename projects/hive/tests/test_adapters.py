"""Adapters against recorded wire formats, through httpx.MockTransport (no network)."""

import json

import httpx
import pytest

from hive.backends import (
    BackendError,
    BackendTimeout,
    BackendUnavailable,
    ChatMessage,
    ChatResult,
    GenParams,
    TextDelta,
    ToolCall,
    ToolSpec,
    create_backend,
)
from hive.config import BackendConfig

from conftest import fast_timeouts


def make(type_, handler, **kw):
    cfg = BackendConfig(name="b", type=type_, url=kw.pop("url", "http://srv:1"), default_model="m", timeouts=fast_timeouts(), **kw)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return create_backend(cfg, client)


async def collect(gen):
    return [ev async for ev in gen]


TOOL = ToolSpec("write_file", "Write a file", {"type": "object", "properties": {"path": {"type": "string"}}})

# --- Ollama -------------------------------------------------------------------------


def ndjson(*chunks):
    return "\n".join(json.dumps(c) for c in chunks) + "\n"


async def test_ollama_stream_text_and_usage():
    seen = {}

    def handler(req: httpx.Request):
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            text=ndjson(
                {"message": {"role": "assistant", "content": "Hel"}, "done": False},
                {"message": {"role": "assistant", "content": "lo"}, "done": False},
                {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop",
                 "prompt_eval_count": 12, "eval_count": 2, "eval_duration": 500_000_000},
            ),
        )

    b = make("ollama", handler, context=2048)
    evs = await collect(b.chat([ChatMessage("user", "hi")], stream=True, params=GenParams(temperature=0.1, max_tokens=50)))
    assert [e.text for e in evs if isinstance(e, TextDelta)] == ["Hel", "lo"]
    res = evs[-1]
    assert isinstance(res, ChatResult) and res.text == "Hello"
    assert res.usage.prompt_tokens == 12 and res.usage.completion_tokens == 2
    assert res.tokens_per_sec == pytest.approx(4.0)
    body = seen["body"]
    assert body["options"]["num_ctx"] == 2048, "num_ctx must always be sent"
    assert body["options"]["temperature"] == 0.1 and body["options"]["num_predict"] == 50
    assert "tools" not in body


async def test_ollama_no_deltas_when_not_streaming():
    def handler(req):
        return httpx.Response(200, text=ndjson({"message": {"content": "x"}, "done": False}, {"done": True}))

    evs = await collect(make("ollama", handler).chat([ChatMessage("user", "hi")]))
    assert len(evs) == 1 and evs[0].text == "x"


async def test_ollama_tool_calls_round_trip():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            text=ndjson(
                {"message": {"content": "", "tool_calls": [{"function": {"name": "write_file", "arguments": {"path": "a.txt"}}}]}, "done": False},
                {"done": True, "eval_count": 5},
            ),
        )

    msgs = [
        ChatMessage("user", "go"),
        ChatMessage("assistant", "", tool_calls=[ToolCall("read_file", {"path": "x"})]),
        ChatMessage("tool", "contents", name="read_file"),
    ]
    res = (await collect(make("ollama", handler).chat(msgs, tools=[TOOL])))[-1]
    assert res.tool_calls == [ToolCall("write_file", {"path": "a.txt"}, None)]
    body = seen["body"]
    assert body["tools"][0]["function"]["name"] == "write_file"
    assert body["messages"][1]["tool_calls"][0]["function"] == {"name": "read_file", "arguments": {"path": "x"}}
    assert body["messages"][2]["tool_name"] == "read_file"


@pytest.mark.parametrize("status,retryable", [(404, False), (400, False), (500, True), (503, True), (429, True)])
async def test_ollama_http_errors(status, retryable):
    b = make("ollama", lambda req: httpx.Response(status, json={"error": "model 'm' not found"}))
    with pytest.raises(BackendError) as ei:
        await collect(b.chat([ChatMessage("user", "hi")]))
    assert ei.value.retryable is retryable
    assert ei.value.status == status
    assert "not found" in str(ei.value)


async def test_ollama_tools_unsupported_switches_capability():
    b = make("ollama", lambda req: httpx.Response(400, json={"error": "registry.ollama.ai/library/gemma3:1b does not support tools"}))
    assert b.capabilities_for("m").tool_calling is True
    with pytest.raises(BackendError) as ei:
        await collect(b.chat([ChatMessage("user", "hi")], tools=[TOOL]))
    assert ei.value.code == "tools_unsupported"
    assert b.capabilities_for("m").tool_calling is False


async def test_ollama_error_inside_stream():
    b = make("ollama", lambda req: httpx.Response(200, text=ndjson({"error": "out of memory"})))
    with pytest.raises(BackendError, match="out of memory"):
        await collect(b.chat([ChatMessage("user", "hi")]))


async def test_connection_refused_is_retryable():
    def handler(req):
        raise httpx.ConnectError("refused")

    with pytest.raises(BackendUnavailable) as ei:
        await collect(make("ollama", handler).chat([ChatMessage("user", "hi")]))
    assert ei.value.retryable


async def test_read_timeout_is_backend_timeout():
    def handler(req):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(BackendTimeout):
        await collect(make("ollama", handler).chat([ChatMessage("user", "hi")]))


async def test_ollama_probe_reads_show():
    def handler(req):
        if req.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion"], "model_info": {"llama.context_length": 4096}})
        return httpx.Response(404)

    b = make("ollama", handler, context=8192)
    caps = await b.probe("m")
    assert caps.tool_calling is False
    assert caps.max_context == 4096  # clamped to what the model was trained for


async def test_ollama_probe_respects_explicit_config():
    b = make("ollama", lambda req: httpx.Response(200, json={"capabilities": ["completion", "tools"]}), tools=False)
    assert (await b.probe("m")).tool_calling is False


async def test_ollama_probe_disabled_for_hailo():
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(404)

    b = make("hailo-ollama", handler, probe=False, tools=False)
    assert (await b.probe("m")).tool_calling is False
    assert calls == []


async def test_ollama_health_and_models():
    def handler(req):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.2:1b", "size": 1}, {"name": "qwen2:1.5b"}]})
        if req.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": "llama3.2:1b"}]})
        return httpx.Response(404)

    b = make("ollama", handler)
    assert [m.name for m in await b.list_models()] == ["llama3.2:1b", "qwen2:1.5b"]
    h = await b.health()
    assert h.ok and h.loaded == ["llama3.2:1b"]


async def test_ollama_health_down():
    def handler(req):
        raise httpx.ConnectError("refused")

    h = await make("ollama", handler).health()
    assert not h.ok and "ConnectError" in h.detail


async def test_model_override_beats_probe():
    b = make("ollama", lambda req: httpx.Response(200, json={}), models={"m": {"context": 1024}})
    assert b.capabilities_for("m").max_context == 1024


# --- OpenAI-compatible --------------------------------------------------------------------


def sse(*chunks):
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


async def test_openai_stream_text_tools_usage():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(
            200,
            text=sse(
                {"choices": [{"delta": {"content": "Sure"}}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "write_", "arguments": '{"pa'}}]}}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "file", "arguments": 'th": "a.txt"}'}}]}, "finish_reason": "tool_calls"}]},
                {"choices": [], "usage": {"prompt_tokens": 30, "completion_tokens": 9}},
            ),
        )

    b = make("openai", handler, url="http://srv:8080", api_key="sekrit")
    evs = await collect(b.chat([ChatMessage("user", "hi")], tools=[TOOL], stream=True, params=GenParams(temperature=0)))
    res = evs[-1]
    assert [e.text for e in evs if isinstance(e, TextDelta)] == ["Sure"]
    assert res.tool_calls == [ToolCall("write_file", {"path": "a.txt"}, "c1")]
    assert res.usage.completion_tokens == 9 and not res.estimated_usage
    assert res.finish_reason == "tool_calls"
    assert seen["url"] == "http://srv:8080/v1/chat/completions"
    assert seen["auth"] == "Bearer sekrit"
    assert seen["body"]["temperature"] == 0
    assert seen["body"]["stream_options"] == {"include_usage": True}


async def test_openai_url_with_v1_and_no_usage():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        return httpx.Response(200, text=sse({"choices": [{"delta": {"content": "one two three"}}]}))

    res = (await collect(make("openai", handler, url="http://srv/v1").chat([ChatMessage("user", "hi")])))[-1]
    assert seen["url"] == "http://srv/v1/chat/completions"
    assert res.text == "one two three" and res.estimated_usage and res.usage.completion_tokens >= 1


async def test_openai_non_streaming():
    def handler(req):
        assert json.loads(req.content)["stream"] is False
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "", "tool_calls": [{"id": "x", "function": {"name": "write_file", "arguments": "{\"path\": \"b\"}"}}]}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        })

    res = (await collect(make("openai", handler, streaming=False).chat([ChatMessage("user", "hi")], tools=[TOOL])))[-1]
    assert res.tool_calls[0].arguments == {"path": "b"}


async def test_openai_tool_result_ids_are_paired():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, text=sse({"choices": [{"delta": {"content": "ok"}}]}))

    msgs = [
        ChatMessage("user", "go"),
        ChatMessage("assistant", "", tool_calls=[ToolCall("read_file", {"path": "x"})]),
        ChatMessage("tool", "data", name="read_file"),
    ]
    await collect(make("openai", handler).chat(msgs))
    m = seen["body"]["messages"]
    assert m[1]["tool_calls"][0]["function"]["arguments"] == '{"path": "x"}'
    assert m[2]["tool_call_id"] == m[1]["tool_calls"][0]["id"]


async def test_openai_models_and_errors():
    def handler(req):
        if req.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "qwen"}, {"id": "llama"}]})
        return httpx.Response(500, json={"error": {"message": "boom"}})

    b = make("openai", handler)
    assert [m.name for m in await b.list_models()] == ["qwen", "llama"]
    assert (await b.health()).ok
    with pytest.raises(BackendError, match="boom") as ei:
        await collect(b.chat([ChatMessage("user", "hi")]))
    assert ei.value.retryable


async def test_unknown_type():
    from hive.config import ConfigError

    with pytest.raises(ConfigError, match="unknown type"):
        create_backend(BackendConfig(name="x", type="nope"))


async def test_plugin_type_by_import_path():
    b = create_backend(BackendConfig(name="x", type="hive.backends.mock:MockBackend"))
    assert type(b).__name__ == "MockBackend"
