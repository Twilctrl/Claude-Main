"""The web_fetch tool against a local HTTP server."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from hive.config import HiveConfig
from hive.tools import ToolError
from hive.tools.web import host_allowed, html_to_text, web_fetch


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/page":
            body = b"<html><head><title>x</title><script>evil()</script></head><body><h1>Bees</h1><p>Bees make &amp; honey.</p></body></html>"
            self._send(200, "text/html; charset=utf-8", body)
        elif self.path == "/redirect-out":
            self.send_response(302)
            self.send_header("Location", "http://evil.example.com/")
            self.end_headers()
        elif self.path == "/big":
            self._send(200, "text/plain", b"x" * 100_000)
        elif self.path == "/image":
            self._send(200, "image/png", b"\x89PNG....")
        else:
            self._send(404, "text/plain", b"nope")

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def ctx(**web):
    hive = HiveConfig.model_validate({"web_fetch": web})
    return SimpleNamespace(run=SimpleNamespace(app=SimpleNamespace(settings=SimpleNamespace(hive=hive))))


async def call(c, **args):
    from hive.tools import get_tool

    return (await get_tool("web_fetch")(c, get_tool("web_fetch").validate(args))).output


async def test_fetch_html_as_text(server):
    out = await call(ctx(), url=f"{server}/page")
    assert "Bees make & honey." in out and "evil()" not in out and "200" in out


async def test_allow_list_and_redirects(server):
    with pytest.raises(ToolError, match="allowed_hosts"):
        await call(ctx(allowed_hosts=["example.org"]), url=f"{server}/page")
    with pytest.raises(ToolError, match="evil.example.com"):
        await call(ctx(allowed_hosts=["127.0.0.1"]), url=f"{server}/redirect-out")


async def test_limits_and_types(server):
    out = await call(ctx(max_bytes=1000), url=f"{server}/big", max_chars=600)
    assert len(out) < 1000
    assert "not shown" in await call(ctx(), url=f"{server}/image")
    with pytest.raises(ToolError, match="http"):
        await call(ctx(), url="file:///etc/passwd")


def test_helpers():
    assert host_allowed("docs.python.org", ["python.org"])
    assert not host_allowed("evilpython.org", ["python.org"])
    assert host_allowed("anything", [])
    assert html_to_text("<p>a</p><p>b</p>").split() == ["a", "b"]
