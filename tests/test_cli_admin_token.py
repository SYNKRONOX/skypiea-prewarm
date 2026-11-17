"""skypiea sessions / trace / explain send the gateway's admin token (issue #7): from
SKYPIEA_ADMIN_TOKEN, else from Secret skypiea-admin read with the user's kubeconfig. A refusal
is reported as such, never as "session not found"."""

from __future__ import annotations

import pytest

pytest.importorskip("click", reason="make setup")
pytest.importorskip("kubernetes", reason="make setup")

import base64
from types import SimpleNamespace

from click.testing import CliRunner

from skypiea_cli import cli as climod
from skypiea_cli.backend import AdminTokenError, KubeBackend


class FakeCore:
    def __init__(self, token: str | None):
        self.token, self.reads = token, 0

    def read_namespaced_secret(self, name, ns):
        self.reads += 1
        assert (name, ns) == ("skypiea-admin", "skypiea-tools")
        if self.token is None:
            raise RuntimeError("404 secret not found")
        return SimpleNamespace(data={"admin-token": base64.b64encode(self.token.encode())
                                     .decode()})

    def list_namespaced_pod(self, ns, label_selector):
        return SimpleNamespace(items=[SimpleNamespace(metadata=SimpleNamespace(name="gw-0"),
                                                      status=SimpleNamespace(phase="Running"))])


def backend(secret: str | None, want: str = "s3cret") -> tuple[KubeBackend, list]:
    kb = KubeBackend.__new__(KubeBackend)
    kb.namespace, kb.core, kb._token = "skypiea-tools", FakeCore(secret), None
    sent: list = []

    def proxy(kind, name, port, path, method="GET", body=None, extra=None):
        sent.append((path, dict(extra or {})))
        if (extra or {}).get("X-Admin-Token") != want:
            return 401, {"error": "bad admin token"}
        if path == "/v1/sessions":
            return 200, {"sessions": [{"session_id": "abc", "calls": 1, "ended": False}]}
        return 200, {"session_id": "abc", "calls": [], "decisions": []}
    kb._proxy = proxy
    return kb, sent


def test_token_read_from_secret_and_sent(monkeypatch):
    monkeypatch.delenv("SKYPIEA_ADMIN_TOKEN", raising=False)
    kb, sent = backend("s3cret")
    assert kb.sessions()[0]["session_id"] == "abc"
    assert kb.session("abc")["gateway_pod"] == "gw-0"
    assert all(h == {"X-Admin-Token": "s3cret"} for _, h in sent)
    assert kb.core.reads == 1                       # read once, then cached


def test_env_token_wins_over_secret(monkeypatch):
    monkeypatch.setenv("SKYPIEA_ADMIN_TOKEN", "from-env")
    kb, sent = backend("s3cret", want="from-env")
    assert kb.sessions()
    assert kb.core.reads == 0 and sent[0][1] == {"X-Admin-Token": "from-env"}


def test_no_token_is_a_clear_error_not_not_found(monkeypatch):
    monkeypatch.delenv("SKYPIEA_ADMIN_TOKEN", raising=False)
    kb, sent = backend(None)
    with pytest.raises(AdminTokenError, match="admin token"):
        kb.session("abc")
    assert sent[0][1] == {}                          # nothing to send, nothing invented
    monkeypatch.setattr(climod, "BACKEND_FACTORY", lambda: backend(None)[0])
    for args in (["sessions"], ["trace", "abc"], ["explain", "abc"]):
        r = CliRunner().invoke(climod.cli, args)
        assert r.exit_code != 0 and "admin token" in r.output and "purged" not in r.output
        assert "s3cret" not in r.output
