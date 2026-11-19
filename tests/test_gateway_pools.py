"""Issue #18: tool calls, the predictor and the controller use separate httpx pools, so 200+
in-flight tool calls cannot starve /decide and /v1/warm of connections."""

from __future__ import annotations

from gateway import main


def test_control_plane_has_its_own_pool(monkeypatch):
    monkeypatch.setenv("SKYPIEA_PREDICTOR_URL", "http://p")
    parts = main.build(main.gateway_config())
    gw, pred, ctrl = parts["gateway"], parts["predictor"], parts["controller"]
    assert gw.http is not pred.client and gw.http is not ctrl.client
    assert pred.client is not ctrl.client
    assert ctrl.headers == {} or "X-Admin-Token" in ctrl.headers
