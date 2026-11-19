"""Shared admin-token check for the platform's mutating endpoints (issues #7, #23, #27).

Server side: ``check(got, expected)`` -> None when allowed, else (status, message). No token
configured = the endpoint is disabled (403), the same secure default as the gateway's session
introspection. Tokens are compared in constant time.

Client side: ``client_token(namespace, context)`` = ``SKYPIEA_ADMIN_TOKEN`` (or ``ADMIN_TOKEN``,
which in-cluster Jobs get from Secret skypiea-admin), else that Secret read with kubectl and the
caller's own kubeconfig. Never printed or stored.
"""

from __future__ import annotations

import base64
import hmac
import os
import subprocess

HEADER = "X-Admin-Token"
SECRET, KEY = "skypiea-admin", "admin-token"


def check(got: str | None, expected: str | None) -> tuple[int, str] | None:
    if not expected:
        return 403, "admin endpoints disabled (no ADMIN_TOKEN configured)"
    if not hmac.compare_digest((got or "").encode("utf-8", "replace"), expected.encode("utf-8")):
        return 401, "missing or bad admin token (X-Admin-Token)"
    return None


def server_token() -> str | None:
    return os.environ.get("ADMIN_TOKEN") or None


def client_token(namespace: str | None = None, context: str | None = None) -> str | None:
    tok = os.environ.get("SKYPIEA_ADMIN_TOKEN") or os.environ.get("ADMIN_TOKEN")
    if tok or not namespace:
        return tok or None
    cmd = ["kubectl", "get", "secret", SECRET, "-n", namespace,
           "-o", "jsonpath={.data.admin-token}"]
    if context:
        cmd += ["--context", context]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        raw = out.stdout.strip() if out.returncode == 0 else ""
        return base64.b64decode(raw, validate=True).decode() if raw else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def headers(token: str | None) -> dict[str, str]:
    return {HEADER: token} if token else {}
