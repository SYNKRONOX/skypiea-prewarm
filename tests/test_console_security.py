"""Live Console exposure: it is a local demo with no authentication, so it must bind to loopback
only and refuse cross-site requests (docs/handoff/REQUESTS_W2.md R9, console/xsite.py):
Host allowlist, same-origin check on every state-changing request, a required custom header
that the console's own JavaScript sends, and no CORS headers."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PORT = 8090
ME = f"http://127.0.0.1:{PORT}"
GOOD = {"Origin": ME, "X-Skypiea-Client": "tests", "Content-Type": "application/json"}


def test_console_binds_to_loopback_only() -> None:
    """console/app.py main() must pass host="127.0.0.1" to uvicorn.run (never 0.0.0.0 or "")."""
    tree = ast.parse((ROOT / "console/app.py").read_text())
    hosts = [kw.value.value for node in ast.walk(tree) if isinstance(node, ast.Call)
             and getattr(node.func, "attr", "") == "run"
             for kw in node.keywords if kw.arg == "host" and isinstance(kw.value, ast.Constant)]
    assert hosts == ["127.0.0.1"]


@pytest.fixture
def console():
    pytest.importorskip("starlette")
    from console.app import Console, make_app
    from console.llm_stream import ScriptedStreamingLLM
    from tests.test_console import FakeStack

    c = Console(ScriptedStreamingLLM([]), base=FakeStack("base"), pred=FakeStack("pred"),
                start_poller=False)
    return c, make_app(c, PORT)


def client(app, base: str = ME):
    from starlette.testclient import TestClient
    return TestClient(app, base_url=base)


def hint(cl, **headers):
    return cl.post("/api/hint", content='{"mode": "enter"}', headers=headers)


def test_cross_site_post_is_refused(console) -> None:
    """R9 repro (was xfail): what a <form> or fetch(mode: "no-cors") on another site sends."""
    c, app = console
    with client(app) as cl:
        r = hint(cl, **{"Origin": "http://evil.example", "Content-Type": "text/plain"})
        assert r.status_code == 403
        assert c.settings["hint"] is False


@pytest.mark.parametrize("origin", ["http://evil.example", "null", f"http://127.0.0.1:{PORT + 1}",
                                    f"https://127.0.0.1:{PORT}", f"http://localhost:{PORT}",
                                    f"{ME}.evil.example", ""])
def test_cross_origin_post_is_refused_even_with_the_header(console, origin) -> None:
    c, app = console
    with client(app) as cl:
        assert hint(cl, **{**GOOD, "Origin": origin}).status_code == 403
    assert c.settings["hint"] is False


def test_post_without_origin_is_refused(console) -> None:
    c, app = console
    with client(app) as cl:
        h = {k: v for k, v in GOOD.items() if k != "Origin"}
        assert hint(cl, **h).status_code == 403
    assert c.settings["hint"] is False


@pytest.mark.parametrize("value", [None, "", "   "])
def test_post_without_the_custom_header_is_refused(console, value) -> None:
    c, app = console
    h = {k: v for k, v in GOOD.items() if k != "X-Skypiea-Client"}
    if value is not None:
        h["X-Skypiea-Client"] = value
    with client(app) as cl:
        r = hint(cl, **h)
        assert r.status_code == 403 and "X-Skypiea-Client" in r.json()["error"]
    assert c.settings["hint"] is False


@pytest.mark.parametrize("host", ["evil.example", f"evil.example:{PORT}",   # DNS rebinding
                                  "127.0.0.1", f"127.0.0.1:{PORT + 1}", f"0.0.0.0:{PORT}",
                                  f"127.0.0.2:{PORT}", f"localhost.evil.example:{PORT}"])
def test_wrong_host_is_refused_for_reads_and_writes(console, host) -> None:
    c, app = console
    with client(app) as cl:
        for r in (cl.get("/api/state", headers={"Host": host}),
                  cl.get("/", headers={"Host": host}),
                  cl.get("/api/export.csv", headers={"Host": host}),
                  hint(cl, **{**GOOD, "Host": host, "Origin": f"http://{host}"})):
            assert r.status_code == 403, (host, r.request.url)
            assert "host not allowed" in r.json()["error"]
    assert c.settings["hint"] is False


@pytest.mark.parametrize("base", [ME, f"http://localhost:{PORT}", f"http://[::1]:{PORT}"])
def test_normal_use_from_each_loopback_name(console, base) -> None:
    c, app = console
    with client(app, base) as cl:
        assert cl.get("/").status_code == 200
        assert cl.get("/api/state").status_code == 200
        r = hint(cl, **{**GOOD, "Origin": base})
        assert r.status_code == 202 and r.json()["settings"]["hint"] is True
        r = cl.post("/api/hint", json={"mode": "off"},
                    headers={"Origin": base, "X-Skypiea-Client": "console page"})
        assert r.status_code == 202 and c.settings["hint"] is False


def test_no_permissive_cors_headers(console) -> None:
    _, app = console
    with client(app) as cl:
        responses = [
            cl.get("/api/state", headers={"Origin": "http://evil.example"}),
            cl.options("/api/hint", headers={
                "Origin": "http://evil.example", "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-skypiea-client,content-type"}),
            hint(cl, **{**GOOD, "Origin": "http://evil.example"}),
            hint(cl, **GOOD)]
        for r in responses:
            assert not [k for k in r.headers if k.lower().startswith("access-control-")]
        assert responses[1].status_code >= 400          # preflight never succeeds


def test_console_page_javascript_sends_the_header_on_every_post() -> None:
    js = (ROOT / "console/static/app.js").read_text()
    posts = [m.start() for m in re.finditer(r'method:\s*"POST"', js)]
    assert len(posts) == 1                              # the one post() helper
    helper = js[js.index("async function post("):js.index("async function post(") + 400]
    assert '"x-skypiea-client"' in helper and 'method: "POST"' in helper
    assert "sendBeacon" not in js and "XMLHttpRequest" not in js


def test_local_scripts_send_origin_and_client() -> None:
    from console.xsite import script_headers
    assert script_headers("x", 9123) == {"Origin": "http://127.0.0.1:9123",
                                         "X-Skypiea-Client": "x"}
    for f in ("demo/console_run.py", "console/bakeoff.py"):
        assert "script_headers(" in (ROOT / f).read_text(), f
