"""A TestClient for the Live Console as its own page would talk to it (R9 guard): Host
127.0.0.1:<console port>, Origin = the console's own origin, X-Skypiea-Client set."""

from __future__ import annotations

from typing import Any


def console_client(app: Any, **headers: str):
    from starlette.testclient import TestClient

    from console.xsite import console_url

    url = console_url()
    return TestClient(app, base_url=url,
                      headers={"Origin": url, "X-Skypiea-Client": "tests", **headers})
