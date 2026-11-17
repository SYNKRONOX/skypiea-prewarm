"""The Live Console reads a session from its gateway with the stack's admin token (issue #7):
read from Secret skypiea-admin with kubectl, cached, re-read once on 401 (stack reinstalled),
and no session data without it."""

from __future__ import annotations

import pytest

pytest.importorskip("httpx", reason="make setup")

import asyncio
import base64

import httpx

from console import stacks
from console.stacks import PRED, StackClient


def gateway(token: str, seen: list):
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.headers.get("x-admin-token"))
        if req.headers.get("x-admin-token") != token:
            return httpx.Response(401, json={"error": "bad admin token"})
        return httpx.Response(200, json={"session_id": "s", "calls": [], "decisions": []})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def secret_reader(values: list[str | None], calls: list):
    async def read(ns, name, key, context):
        calls.append((ns, name, key, context))
        v = values[min(len(calls), len(values)) - 1]
        return (0, base64.b64encode(v.encode()).decode()) if v else (1, "")
    return read


def test_token_from_secret_sent_and_cached(monkeypatch):
    monkeypatch.delenv("SKYPIEA_ADMIN_TOKEN_PRED", raising=False)
    calls, seen = [], []
    monkeypatch.setattr(stacks, "read_secret", secret_reader(["tok"], calls))
    sc = StackClient(PRED, gateway("tok", seen), context="kind-x")
    assert asyncio.run(sc.session("s"))["session_id"] == "s"
    assert asyncio.run(sc.session("s"))["session_id"] == "s"
    assert seen == ["tok", "tok"]
    assert calls == [("skypiea-pred", "skypiea-admin", "admin-token", "kind-x")]


def test_rotated_token_is_re_read_once(monkeypatch):
    monkeypatch.delenv("SKYPIEA_ADMIN_TOKEN_PRED", raising=False)
    calls, seen = [], []
    monkeypatch.setattr(stacks, "read_secret", secret_reader(["old", "new"], calls))
    sc = StackClient(PRED, gateway("new", seen))
    assert asyncio.run(sc.session("s"))["session_id"] == "s"
    assert seen == ["old", "new"] and len(calls) == 2


def test_no_secret_means_no_session_data_and_no_request(monkeypatch):
    monkeypatch.delenv("SKYPIEA_ADMIN_TOKEN_PRED", raising=False)
    calls, seen = [], []
    monkeypatch.setattr(stacks, "read_secret", secret_reader([None], calls))
    sc = StackClient(PRED, gateway("tok", seen))
    assert asyncio.run(sc.session("s")) is None
    assert asyncio.run(sc.session("s")) is None
    assert seen == [] and len(calls) == 1      # one Secret read, then backoff (30 s)
    assert asyncio.run(sc.admin_token()) is None and len(calls) == 1


def test_env_override(monkeypatch):
    monkeypatch.setenv("SKYPIEA_ADMIN_TOKEN_PRED", "envtok")
    calls, seen = [], []
    monkeypatch.setattr(stacks, "read_secret", secret_reader([None], calls))
    sc = StackClient(PRED, gateway("envtok", seen))
    assert asyncio.run(sc.session("s"))["session_id"] == "s" and calls == []
