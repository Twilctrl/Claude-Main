"""Ollama's native API (``/api/chat``). Also covers hailo-ollama and the CerberOS gate.

hailo-ollama speaks a subset of the API: no tools, no ``/api/show``, one
request at a time. Configure it with ``tools: false``, ``probe: false`` and
``concurrency: 1`` rather than a separate adapter.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

from .base import (
    BackendError,
    Capabilities,
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


def _msg(m: ChatMessage) -> dict[str, Any]:
    d: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        d["tool_calls"] = [{"function": {"name": c.name, "arguments": c.arguments}} for c in m.tool_calls]
    if m.role == "tool" and m.name:
        d["tool_name"] = m.name
    return d


def _tool(t: ToolSpec) -> dict[str, Any]:
    return {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}


def _calls(raw: list[dict[str, Any]] | None) -> list[ToolCall]:
    out = []
    for c in raw or []:
        fn = c.get("function") or {}
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {"_raw": args}
        out.append(ToolCall(name=fn.get("name", ""), arguments=args if isinstance(args, dict) else {}, id=c.get("id")))
    return out


class OllamaBackend(ModelBackend):
    type_name = "ollama"

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
        caps = self.capabilities_for(model)
        options: dict[str, Any] = {"num_ctx": caps.max_context}  # without it Ollama silently truncates
        if params.temperature is not None:
            options["temperature"] = params.temperature
        if params.max_tokens is not None:
            options["num_predict"] = params.max_tokens
        if params.top_p is not None:
            options["top_p"] = params.top_p
        if params.stop:
            options["stop"] = params.stop
        if params.seed is not None:
            options["seed"] = params.seed
        options.update(self.config.options.get("ollama_options", {}))
        body: dict[str, Any] = {
            "model": model,
            "messages": [_msg(m) for m in messages],
            "stream": self.config.streaming,
            "options": options,
        }
        if tools:
            body["tools"] = [_tool(t) for t in tools]
        if self.config.keep_alive:
            body["keep_alive"] = self.config.keep_alive

        t = self.config.timeouts
        clock = Stopwatch()
        text: list[str] = []
        calls: list[ToolCall] = []
        final: dict[str, Any] = {}
        with transport_errors(self.name):
            async with self.client.stream("POST", f"{self.config.url}/api/chat", json=body, headers=self._headers()) as resp:
                if resp.status_code >= 400:
                    err = http_error(resp.status_code, (await resp.aread()).decode(errors="replace"), self.name)
                    if err.code == "tools_unsupported":  # remember, so callers switch to the text protocol
                        self._model_caps[model] = replace(caps, tool_calling=False)
                    raise err
                async for line in with_timeouts(resp.aiter_lines(), t.first_token, t.idle, self.name):
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except ValueError:
                        raise BackendError(f"{self.name}: bad JSON from server: {line[:200]}", retryable=True) from None
                    if "error" in chunk:
                        raise BackendError(f"{self.name}: {chunk['error']}", retryable=False)
                    msg = chunk.get("message") or {}
                    piece = msg.get("content") or ""
                    if piece:
                        clock.tick()
                        text.append(piece)
                        if stream:
                            yield TextDelta(piece)
                    if msg.get("tool_calls"):
                        clock.tick()
                        calls.extend(_calls(msg["tool_calls"]))
                    if chunk.get("done"):
                        final = chunk
                        break

        full = "".join(text)
        prompt_tokens = final.get("prompt_eval_count")
        completion = final.get("eval_count")
        gen = final.get("eval_duration")
        yield ChatResult(
            text=full,
            tool_calls=calls,
            usage=Usage(prompt_tokens, completion if completion is not None else self.estimate_tokens(full)),
            model=model,
            backend=self.name,
            total_seconds=clock.total,
            gen_seconds=gen / 1e9 if gen else clock.gen,
            finish_reason=final.get("done_reason"),
            estimated_usage=completion is None,
        )

    async def list_models(self) -> list[ModelInfo]:
        with transport_errors(self.name):
            resp = await self.client.get(f"{self.config.url}/api/tags", timeout=self.config.timeouts.connect + 5, headers=self._headers())
        if resp.status_code >= 400:
            raise http_error(resp.status_code, resp.text, self.name)
        return [
            ModelInfo(name=m.get("name") or m.get("model", ""), size=m.get("size"), details=m.get("details") or {})
            for m in resp.json().get("models", [])
        ]

    async def health(self) -> Health:
        start = time.monotonic()
        try:
            models = await self.list_models()
        except BackendError as e:
            return Health(ok=False, detail=str(e))
        latency = (time.monotonic() - start) * 1000
        loaded: list[str] = []
        try:
            with transport_errors(self.name):
                r = await self.client.get(f"{self.config.url}/api/ps", timeout=3, headers=self._headers())
            if r.status_code == 200:
                loaded = [m.get("name", "") for m in r.json().get("models", [])]
        except (BackendError, ValueError):
            pass  # hailo-ollama has no /api/ps
        return Health(ok=True, latency_ms=latency, detail=f"{len(models)} models", loaded=loaded)

    async def probe(self, model: str | None = None) -> Capabilities:
        """Read tool support and context length from ``/api/show`` (newer Ollama only)."""
        model = self.model_name(model)
        base = self.capabilities_for(model)
        if not self.config.probe or model in self._model_caps:
            return base
        try:
            with transport_errors(self.name):
                r = await self.client.post(f"{self.config.url}/api/show", json={"model": model}, timeout=10, headers=self._headers())
            if r.status_code != 200:
                return base
            info = r.json()
        except (BackendError, ValueError):
            return base
        caps = self.capabilities
        if self.config.tools is None and isinstance(info.get("capabilities"), list):
            caps = replace(caps, tool_calling="tools" in info["capabilities"])
        trained = next(
            (v for k, v in (info.get("model_info") or {}).items() if k.endswith(".context_length") and isinstance(v, int)),
            None,
        )
        if trained:
            caps = replace(caps, max_context=min(caps.max_context, trained))
        self._model_caps[model] = caps
        return self.capabilities_for(model)
