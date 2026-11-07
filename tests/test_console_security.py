"""Live Console exposure: it is a local demo with no authentication, so it must bind to loopback
only, and (requested in docs/handoff/REQUESTS_W2.md R9) refuse settings changes sent by other
web sites from the presenter's browser."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_console_binds_to_loopback_only() -> None:
    """console/app.py main() must pass host="127.0.0.1" to uvicorn.run (never 0.0.0.0 or "")."""
    tree = ast.parse((ROOT / "console/app.py").read_text())
    hosts = [kw.value.value for node in ast.walk(tree) if isinstance(node, ast.Call)
             and getattr(node.func, "attr", "") == "run"
             for kw in node.keywords if kw.arg == "host" and isinstance(kw.value, ast.Constant)]
    assert hosts == ["127.0.0.1"]


@pytest.mark.xfail(strict=False, reason="REQUESTS_W2 R9: console POST endpoints accept "
                   "cross-site requests (no Origin/Content-Type check), so any web page open "
                   "in the presenter's browser can change settings")
def test_cross_site_post_is_refused() -> None:
    pytest.importorskip("starlette")
    from starlette.testclient import TestClient

    from console.app import Console, make_app
    from console.llm_stream import ScriptedStreamingLLM
    from tests.test_console import FakeStack

    c = Console(ScriptedStreamingLLM([]), base=FakeStack("base"), pred=FakeStack("pred"),
                start_poller=False)
    with TestClient(make_app(c)) as cl:
        # what a <form> or fetch(mode: "no-cors") on another site can send without a preflight
        r = cl.post("/api/hint", content='{"mode": "enter"}',
                    headers={"Origin": "http://evil.example", "Content-Type": "text/plain"})
        assert r.status_code == 403
        assert c.settings["hint"] is False
