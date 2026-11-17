"""Issue #7 / GHSA-r23c-9c4p-2vxp: the session introspection endpoints (GET /v1/sessions and
GET /v1/sessions/{sid}) listed every session id and its calls/decisions to any caller on the
cluster network. A listed id is the capability for POST /mcp (append calls) and DELETE /mcp
(terminate the session), so the listing had to be closed.

Now: they need the admin token (X-Admin-Token, constant-time compare, the predictor's
mechanism); with no ADMIN_TOKEN configured they are disabled (403). Session ids the gateway
assigns carry at least 128 bits from `secrets`."""

from __future__ import annotations

import pytest

pytest.importorskip("httpx", reason="make setup")
pytest.importorskip("starlette", reason="make setup")
pytest.importorskip("prometheus_client", reason="make setup")

import asyncio
import base64

import httpx

from gateway.app import new_session_id
from tests.test_gateway import call, make, post

TOKEN = "t0ken-for-tests-only"


async def _get(gw, path: str, token: str | None = None) -> httpx.Response:
    h = {"X-Admin-Token": token} if token is not None else {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(gw.app(background=False)),
                                 base_url="http://gw") as c:
        return await c.get(path, headers=h)


def _with_session(**cfg):
    gw, _, _, _ = make(ready={"shell": 1}, **cfg)
    asyncio.run(post(gw, call("shell"), sid="victim"))
    return gw


def test_unauthenticated_list_does_not_leak_session_ids():
    """The reproduction of issue #7: before the fix this returned 200 with "victim"."""
    gw = _with_session(admin_token=TOKEN)
    r = asyncio.run(_get(gw, "/v1/sessions"))
    assert r.status_code == 401
    assert "victim" not in r.text


@pytest.mark.parametrize("path", ["/v1/sessions", "/v1/sessions/victim"])
@pytest.mark.parametrize("token", [None, "", "wrong", TOKEN + "x"])
def test_missing_or_wrong_token_is_refused(path, token):
    gw = _with_session(admin_token=TOKEN)
    r = asyncio.run(_get(gw, path, token))
    assert r.status_code == 401
    assert "victim" not in r.text and "calls" not in r.json()


@pytest.mark.parametrize("path", ["/v1/sessions", "/v1/sessions/victim"])
@pytest.mark.parametrize("token", [None, "", TOKEN])
def test_no_token_configured_disables_the_endpoints(path, token):
    """Secure default: without ADMIN_TOKEN nobody can read sessions, not even with a token."""
    gw = _with_session()
    assert gw.cfg.admin_token is None
    r = asyncio.run(_get(gw, path, token))
    assert r.status_code == 403
    assert "victim" not in r.text


def test_right_token_reads_list_and_one_session():
    gw = _with_session(admin_token=TOKEN)
    r = asyncio.run(_get(gw, "/v1/sessions", TOKEN))
    assert r.status_code == 200 and r.json()["sessions"][0]["session_id"] == "victim"
    r = asyncio.run(_get(gw, "/v1/sessions/victim", TOKEN))
    assert r.status_code == 200 and r.json()["calls"][0]["tool_class"] == "shell"
    assert asyncio.run(_get(gw, "/v1/sessions/nope", TOKEN)).status_code == 404


def test_unknown_session_with_bad_token_is_401_not_404():
    """No existence oracle: a wrong token never tells whether an id exists."""
    gw = _with_session(admin_token=TOKEN)
    assert asyncio.run(_get(gw, "/v1/sessions/nope", "wrong")).status_code == 401
    assert asyncio.run(_get(gw, "/v1/sessions/victim", "wrong")).status_code == 401


def test_mcp_and_health_stay_open():
    """The fix closes introspection only; the MCP protocol and /healthz are unchanged."""
    gw, _, _, _ = make(ready={"shell": 1}, admin_token=TOKEN)
    r = asyncio.run(post(gw, call("shell"), sid=None))
    assert r.status_code == 200 and "result" in r.json()
    assert asyncio.run(_get(gw, "/healthz")).status_code == 200


def test_gateway_assigned_session_ids_are_unguessable():
    ids = {new_session_id() for _ in range(2000)}
    assert len(ids) == 2000
    for sid in list(ids)[:50]:
        raw = base64.urlsafe_b64decode(sid + "=" * (-len(sid) % 4))
        assert len(raw) * 8 >= 128
        assert sid.isascii() and sid.isprintable() and " " not in sid
    gw, _, _, _ = make(ready={"shell": 1})
    r = asyncio.run(post(gw, call("shell"), sid=None))
    sid = r.headers["Mcp-Session-Id"]
    assert len(base64.urlsafe_b64decode(sid + "=" * (-len(sid) % 4))) * 8 >= 128


def test_gateway_main_reads_admin_token_from_env(monkeypatch):
    from gateway import main
    monkeypatch.setenv("ADMIN_TOKEN", "abc")
    assert main.gateway_config().admin_token == "abc"
    monkeypatch.setenv("ADMIN_TOKEN", "")
    assert main.gateway_config().admin_token is None
    monkeypatch.delenv("ADMIN_TOKEN")
    assert main.gateway_config().admin_token is None
