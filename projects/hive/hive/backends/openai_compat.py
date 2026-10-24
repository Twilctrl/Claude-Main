"""OpenAI-compatible ``/v1/chat/completions``: llama.cpp server, LM Studio, vLLM, and others.

``url`` may be given with or without the trailing ``/v1``.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

from .base import (
    BackendError,
    ChatEvent,
    ChatMessage,
    ChatResult,
    GenParams,
    Health,
    ModelBackend,
    ModelInfo,
    Stopwatch,
    TextDelta,
    ToolCall,
    ToolSpec,
    Usage,
    http_error,
    transport_errors,
    with_timeouts,
)


def _msgs(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    pending_ids: list[str] = []  # ids of the last assistant tool calls, for tool results without one
    for m in messages:
        d: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_calls:
            pending_ids = []
            calls = []
            for c in m.tool_calls:
                cid = c.id or f"call_{uuid.uuid4().hex[:12]}"
                pending_ids.append(cid)
                calls.append({"id": cid, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}})
            d["tool_calls"] = calls
        if m.role == "tool":
            d["tool_call_id"] = m.tool_call_id or (pending_ids.pop(0) if pending_ids else f"call_{uuid.uuid4().hex[:12]}")
            if m.name:
                d["name"] = m.name
        out.append(d)
    return out


class OpenAIBackend(ModelBackend):
    type_name = "openai"

    @property
    def base(self) -> str:
        url = self.config.url
        return url if url.endswith("/v1") else f"{url}/v1"

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        stream: bool = False,
        *,
        model: str | None = None,
        params: GenParams | None = None,
    ) -> AsyncIterator[ChatEvent]:
        model = self.model_name(model)
        params = params or GenParams()
        body: dict[str, Any] = {"model": model, "messages": _msgs(messages), "stream": self.config.streaming}
        if self.config.streaming and self.config.options.get("stream_usage", True):
            body["stream_options"] = {"include_usage": True}
        if tools:
            body["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools
            ]
        for k in ("temperature", "max_tokens", "top_p", "stop", "seed"):
            v = getattr(params, k)
            if v is not None:
                body[k] = v
        body.update(self.config.options.get("extra_body", {}))

        t = self.config.timeouts
        clock = Stopwatch()
        url = f"{self.base}/chat/completions"
        with transport_errors(self.name):
            if not self.config.streaming:
                resp = await self.client.post(url, json=body, timeout=t.total, headers=self._headers())
                if resp.status_code >= 400:
                    raise self._error(resp.status_code, resp.text, model)
                data = resp.json()
                choice = (data.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                text = msg.get("content") or ""
                clock.tick()
                if stream and text:
                    yield TextDelta(text)
                usage = data.get("usage") or {}
                yield self._result(text, self._calls(msg.get("tool_calls")), usage, model, clock, choice.get("finish_reason"))
                return

            text_parts: list[str] = []
            partial: dict[int, dict[str, Any]] = {}  # tool calls arrive in fragments, by index
            usage: dict[str, Any] = {}
            finish: str | None = None
            async with self.client.stream("POST", url, json=body, headers=self._headers()) as resp:
                if resp.status_code >= 400:
                    raise self._error(resp.status_code, (await resp.aread()).decode(errors="replace"), model)
                async for line in with_timeouts(resp.aiter_lines(), t.first_token, t.idle, self.name):
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except ValueError:
                        raise BackendError(f"{self.name}: bad JSON in stream: {payload[:200]}", retryable=True) from None
                    if "error" in chunk:
                        err = chunk["error"]
                        raise BackendError(f"{self.name}: {err.get('message', err) if isinstance(err, dict) else err}")
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        piece = delta.get("content") or ""
                        if piece:
                            clock.tick()
                            text_parts.append(piece)
                            if stream:
                                yield TextDelta(piece)
                        for tc in delta.get("tool_calls") or []:
                            clock.tick()
                            slot = partial.setdefault(tc.get("index", len(partial)), {"id": None, "name": "", "args": ""})
                            slot["id"] = tc.get("id") or slot["id"]
                            fn = tc.get("function") or {}
                            slot["name"] += fn.get("name") or ""
                            slot["args"] += fn.get("arguments") or ""
                        if choice.get("finish_reason"):
                            finish = choice["finish_reason"]

        calls = [
            {"id": p["id"], "function": {"name": p["name"], "arguments": p["args"]}} for _, p in sorted(partial.items())
        ]
        yield self._result("".join(text_parts), self._calls(calls), usage, model, clock, finish)

    def _error(self, status: int, body: str, model: str) -> BackendError:
        err = http_error(status, body, self.name)
        if err.code == "tools_unsupported":
            self._model_caps[model] = replace(self.capabilities_for(model), tool_calling=False)
        return err

    @staticmethod
    def _calls(raw: list[dict[str, Any]] | None) -> list[ToolCall]:
        out = []
        for c in raw or []:
            fn = c.get("function") or {}
            args: Any = fn.get("arguments") or "{}"
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except ValueError:
                    args = {"_raw": args}
            out.append(ToolCall(name=fn.get("name", ""), arguments=args if isinstance(args, dict) else {}, id=c.get("id")))
        return out

    def _result(
        self, text: str, calls: list[ToolCall], usage: dict[str, Any], model: str, clock: Stopwatch, finish: str | None
    ) -> ChatResult:
        completion = usage.get("completion_tokens")
        return ChatResult(
            text=text,
            tool_calls=calls,
            usage=Usage(usage.get("prompt_tokens"), completion if completion is not None else self.estimate_tokens(text)),
            model=model,
            backend=self.name,
            total_seconds=clock.total,
            gen_seconds=clock.gen,
            finish_reason=finish,
            estimated_usage=completion is None,
        )

    async def list_models(self) -> list[ModelInfo]:
        with transport_errors(self.name):
            resp = await self.client.get(f"{self.base}/models", timeout=self.config.timeouts.connect + 5, headers=self._headers())
        if resp.status_code >= 400:
            raise http_error(resp.status_code, resp.text, self.name)
        data = resp.json()
        items = data.get("data", data.get("models", [])) if isinstance(data, dict) else data
        return [ModelInfo(name=m.get("id") or m.get("name", ""), details=m) for m in items]

    async def health(self) -> Health:
        start = time.monotonic()
        try:
            models = await self.list_models()
        except BackendError as e:
            return Health(ok=False, detail=str(e))
        return Health(ok=True, latency_ms=(time.monotonic() - start) * 1000, detail=f"{len(models)} models")
