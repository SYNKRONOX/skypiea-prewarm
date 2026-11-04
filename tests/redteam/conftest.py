"""Shared fixtures: a bundle trained on the committed samples and a TestClient."""

from __future__ import annotations

from tests.test_service import TOKEN, client, env  # noqa: F401 - re-exported fixtures

__all__ = ["TOKEN", "client", "env"]
