"""Entry points for the platform image:
    python -m gateway.main gateway      # the MCP gateway (port 8080)
    python -m gateway.main predictor    # the predictor placeholder (port 8080)
Configuration from environment variables (see charts/skypiea values `platform`)."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import httpx
import uvicorn

from gateway.app import Gateway, GatewayConfig
from gateway.controller_client import HttpControllerClient
from gateway.predictor import HttpPredictorClient
from gateway.readiness import K8sEndpointWatcher, ReadyTracker
from gateway.routing import Router
from tools.common.toolmap import k8s_name


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def gateway() -> None:
    ns = _env("SKYPIEA_NAMESPACE", "skypiea-tools")
    ttl = _env("SKYPIEA_DEMAND_TTL_S", "0")
    cfg = GatewayConfig(
        namespace=ns, hold_ms=float(_env("SKYPIEA_HOLD_MS", "150")),
        cold_timeout_s=float(_env("SKYPIEA_COLD_TIMEOUT_S", "120")),
        predictor_timeout_s=float(_env("SKYPIEA_PREDICTOR_TIMEOUT_MS", "300")) / 1000,
        demand_ttl_s=None if ttl == "" else int(ttl),
        session_idle_s=float(_env("SKYPIEA_SESSION_IDLE_S", "600")),
    )
    http = httpx.AsyncClient(limits=httpx.Limits(max_connections=200))
    router = Router(ns)
    pred_url = _env("SKYPIEA_PREDICTOR_URL", "")
    predictor = HttpPredictorClient(pred_url, cfg.predictor_timeout_s, http) if pred_url else None
    controller = HttpControllerClient(_env("SKYPIEA_CONTROLLER_URL",
                                           f"http://skypiea-controller.{ns}.svc:8081"), 0.5, http)
    tracker = ReadyTracker()
    gw = Gateway(cfg, router, predictor, controller, tracker, http)
    app = gw.app()

    async def run() -> None:
        K8sEndpointWatcher(tracker, ns, {k8s_name(s): s for s in router.servers()},
                           asyncio.get_running_loop()).start()
        await uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=int(_env("PORT", "8080")),
                                            log_level="warning", access_log=False)).serve()

    asyncio.run(run())


def predictor() -> None:
    from gateway.predictor_stub import create_app

    uvicorn.run(create_app(), host="0.0.0.0", port=int(_env("PORT", "8080")),
                log_level="warning", access_log=False)


if __name__ == "__main__":
    logging.basicConfig(level=_env("LOG_LEVEL", "INFO"),
                        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s",'
                               '"msg":"%(message)s"}')
    {"gateway": gateway, "predictor": predictor}[sys.argv[1]]()
