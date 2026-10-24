"""Tools agents can call, the ``@tool`` decorator, and the registry.

A tool is an async function whose first parameter is a :class:`ToolContext`.
Its JSON schema comes from the type hints and the docstring::

    from hive.tools import tool

    @tool(risky=False)
    async def word_count(ctx, path: str) -> str:
        \"\"\"Count the words in a workspace file.

        Args:
            path: file to count, relative to the project folder
        \"\"\"
        text = ctx.run.workspace.read_bytes(path).decode()
        return f"{len(text.split())} words"

Agents only see tools their definition lists. ``risky`` tools (or a callable
that decides per call) go through the approval policy first.
"""

from __future__ import annotations

import inspect
import re
import typing
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, ValidationError, create_model

from ..backends import ToolSpec

if TYPE_CHECKING:
    from ..agents.agent import Agent
    from ..runtime.run import Run


class ToolError(Exception):
    """A tool failed in a way the agent should hear about and can fix."""


@dataclass
class ToolContext:
    run: Run
    agent: Agent


@dataclass
class ToolResult:
    """What a tool returns. A plain string return becomes ``ToolResult(output=...)``."""

    output: str
    ok: bool = True
    data: dict[str, Any] = field(default_factory=dict)  # extra detail for events and the UI
    progress: bool = False  # the run moved forward (files, tasks, board); resets stall detection
    end_turn: bool = False  # stop the agent's turn after this call, with ``output`` as its reply


RiskFn = Callable[[ToolContext, dict[str, Any]], bool]


@dataclass
class Tool:
    name: str
    description: str
    fn: Callable[..., Awaitable[ToolResult | str]]
    args_model: type[BaseModel]
    risky: bool | RiskFn = False
    group: str = "plugin"
    source: str = "builtin"

    @property
    def parameters(self) -> dict[str, Any]:
        return _clean_schema(self.args_model.model_json_schema())

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.parameters)

    def validate(self, args: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(args, dict):
            raise ToolError(f"{self.name}: arguments must be a JSON object")
        try:
            return self.args_model.model_validate(args).model_dump()
        except ValidationError as e:
            problems = []
            for err in e.errors():
                loc = ".".join(str(x) for x in err["loc"]) or "arguments"
                msg = "is required" if err["type"] == "missing" else err["msg"].lower()
                problems.append(f"'{loc}' {msg}")
            raise ToolError(f"{self.name}: {'; '.join(problems)}") from None

    def is_risky(self, ctx: ToolContext, args: dict[str, Any]) -> bool:
        return self.risky(ctx, args) if callable(self.risky) else bool(self.risky)

    def signature(self) -> str:
        """``name(arg: type, opt?: type)`` for the text tool protocol."""
        props = self.parameters.get("properties", {})
        req = set(self.parameters.get("required", []))
        parts = [f"{k}{'' if k in req else '?'}: {_type_name(v)}" for k, v in props.items()]
        return f"{self.name}({', '.join(parts)})"

    async def __call__(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        out = await self.fn(ctx, **args)
        return out if isinstance(out, ToolResult) else ToolResult(output=str(out))


_REGISTRY: dict[str, Tool] = {}
_REGISTERED: list[str] = []  # every registration in order, so plugin loading can tell what a file added


def register(t: Tool) -> Tool:
    _REGISTRY[t.name] = t
    _REGISTERED.append(t.name)
    return t


def get_tool(name: str) -> Tool | None:
    return _REGISTRY.get(name)


def all_tools() -> dict[str, Tool]:
    return dict(_REGISTRY)


def tool(
    name: str | None = None,
    *,
    description: str | None = None,
    risky: bool | RiskFn = False,
    group: str = "plugin",
):
    """Decorator: register an async function as a tool (see the module docstring)."""

    def deco(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        t = make_tool(fn, name=name, description=description, risky=risky, group=group)
        t.source = getattr(fn, "__module__", "plugin")
        register(t)
        return fn

    return deco


def make_tool(
    fn: Callable[..., Awaitable[Any]],
    *,
    name: str | None = None,
    description: str | None = None,
    risky: bool | RiskFn = False,
    group: str = "plugin",
) -> Tool:
    if not inspect.iscoroutinefunction(fn):
        raise TypeError(f"tool {fn.__name__} must be an async function")
    tname = name or fn.__name__
    summary, arg_docs = _parse_doc(inspect.getdoc(fn) or "")
    hints = typing.get_type_hints(fn, include_extras=True)
    params = list(inspect.signature(fn).parameters.values())
    if not params or params[0].name not in ("ctx", "context"):
        raise TypeError(f"tool {tname}: the first parameter must be ctx")
    fields: dict[str, Any] = {}
    from pydantic import Field

    for p in params[1:]:
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        ann = hints.get(p.name, str)
        default = ... if p.default is inspect.Parameter.empty else p.default
        if isinstance(default, (list, dict)):
            default_value = default
            fields[p.name] = (ann, Field(default_factory=lambda d=default_value: type(d)(d), description=arg_docs.get(p.name)))
        else:
            fields[p.name] = (ann, Field(default, description=arg_docs.get(p.name)))
    model = create_model(f"{tname}_args", __config__=ConfigDict(extra="ignore"), **fields)
    return Tool(tname, description or summary or tname, fn, model, risky, group)


def _parse_doc(doc: str) -> tuple[str, dict[str, str]]:
    """The first paragraph, and ``name: text`` lines under ``Args:``."""
    summary = doc.split("\n\n", 1)[0].replace("\n", " ").strip()
    args: dict[str, str] = {}
    m = re.search(r"^\s*Args:\s*$(.*?)(?=^\s*\w[\w ]*:\s*$|\Z)", doc, re.S | re.M)
    if m:
        current = None
        for line in m.group(1).splitlines():
            am = re.match(r"^\s+(\w+)\s*(?:\(.*?\))?:\s*(.*)$", line)
            if am:
                current = am.group(1)
                args[current] = am.group(2).strip()
            elif current and line.strip():
                args[current] += " " + line.strip()
    return summary, args


def _clean_schema(s: Any) -> Any:
    """Drop pydantic's titles and inline $defs, so small models see a compact schema."""
    defs = s.get("$defs", {}) if isinstance(s, dict) else {}

    def walk(x: Any) -> Any:
        if isinstance(x, dict):
            if "$ref" in x:
                return walk(defs.get(x["$ref"].split("/")[-1], {}))
            if "anyOf" in x:
                opts = [o for o in x["anyOf"] if o.get("type") != "null"]
                if len(opts) == 1:
                    merged = {**{k: v for k, v in x.items() if k != "anyOf"}, **opts[0]}
                    return walk(merged)
            out = {}
            for k, v in x.items():
                if k == "properties" and isinstance(v, dict):
                    out[k] = {name: walk(sub) for name, sub in v.items()}  # property names are data, keep them all
                elif k not in ("title", "$defs"):
                    out[k] = walk(v)
            return out
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x

    return walk(s)


def _type_name(prop: dict[str, Any]) -> str:
    t = prop.get("type", "any")
    if t == "array":
        return f"list[{_type_name(prop.get('items', {}))}]"
    if "enum" in prop:
        return "|".join(repr(v) for v in prop["enum"])
    return {"string": "string", "integer": "int", "number": "number", "boolean": "bool", "object": "object"}.get(t, str(t))


__all__ = [
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolResult",
    "all_tools",
    "get_tool",
    "make_tool",
    "register",
    "tool",
]
