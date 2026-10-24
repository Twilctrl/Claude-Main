"""The ``web_fetch`` tool: off unless an agent lists it.

GET only, with a size limit, a timeout, at most 5 redirects, and an optional
host allow-list (``web_fetch.allowed_hosts`` in hive.yaml) checked on every
hop. HTML is reduced to text. Fetched pages can carry prompt injection, so
give this tool only to agents that need it.
"""

from __future__ import annotations

import html
import re
from urllib.parse import urljoin, urlparse

import httpx

from .. import __version__
from ..context import clip
from . import ToolContext, ToolError, tool

_DROP = re.compile(r"<(script|style|noscript|svg|head)\b.*?</\1\s*>", re.S | re.I)
_BREAK = re.compile(r"<(br|/p|/div|/h[1-6]|/li|/tr|/section|/article)\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")


def html_to_text(page: str) -> str:
    page = _DROP.sub(" ", page)
    page = _BREAK.sub("\n", page)
    page = _TAG.sub(" ", page)
    page = html.unescape(page)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in page.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(ln for ln in lines if ln))


def host_allowed(host: str, allowed: list[str]) -> bool:
    if not allowed:
        return True
    host = host.lower().rstrip(".")
    return any(host == a.lower() or host.endswith("." + a.lower().lstrip(".")) for a in allowed)


@tool(group="web")
async def web_fetch(ctx: ToolContext, url: str, max_chars: int = 8000) -> str:
    """Fetch a web page (or a page on your local network) and return its text.

    Args:
        url: the full http:// or https:// address
        max_chars: how much of the text to return
    """
    cfg = ctx.run.app.settings.hive.web_fetch
    async with httpx.AsyncClient(timeout=cfg.timeout, follow_redirects=False, headers={"User-Agent": f"hive/{__version__}"}) as client:
        current = url
        for _ in range(6):
            u = urlparse(current)
            if u.scheme not in ("http", "https") or not u.hostname:
                raise ToolError(f"only http(s) URLs can be fetched, not {current!r}")
            if not host_allowed(u.hostname, cfg.allowed_hosts):
                raise ToolError(f"{u.hostname} isn't in web_fetch.allowed_hosts")
            try:
                async with client.stream("GET", current) as resp:
                    if resp.is_redirect and "location" in resp.headers:
                        current = urljoin(current, resp.headers["location"])
                        continue
                    body = bytearray()
                    async for chunk in resp.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > cfg.max_bytes:
                            break
                    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    status = resp.status_code
                    charset = resp.charset_encoding or "utf-8"
            except httpx.HTTPError as e:
                raise ToolError(f"fetching {current} failed: {type(e).__name__}: {e}") from None
            if ctype and not (ctype.startswith("text/") or ctype in ("application/json", "application/xml", "application/xhtml+xml")):
                return f"{current} ({status}): {ctype} content, {len(body)} bytes; not shown"
            text = body.decode(charset, "replace")
            if "html" in ctype:
                text = html_to_text(text)
            return f"{current} ({status}, {ctype or 'unknown type'})\n\n{clip(text, max(500, min(max_chars, 50_000)))}"
        raise ToolError("too many redirects")
