"""kopf operator: watches the tool Deployments and runs the ControllerService.

    kopf run --standalone --namespace skypiea-tools -m controller.operator

Only Deployments labelled skypiea.io/tier (the tool servers) are touched. On startup the pool
is rebuilt from the live Deployments (replicas + skypiea/last-used, skypiea/ttl-s) before the
reconcile loop may scale anything, so a restart never scales a warm tool down early.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

import kopf
from aiohttp import web

from controller.core import PoolConfig
from controller.service import ControllerService, K8sScaler

NS = os.environ.get("SKYPIEA_NAMESPACE", "skypiea-tools")
TOOL_SELECTOR = {"skypiea.io/tier": kopf.PRESENT}
log = logging.getLogger("skypiea.controller")
_svc: ControllerService | None = None


def _cfg() -> PoolConfig:
    e = os.environ.get
    return PoolConfig(
        default_ttl_s=float(e("SKYPIEA_DEFAULT_TTL_S", "30")),
        purge_s=float(e("SKYPIEA_PURGE_S", "5")),
        min_dwell_s=float(e("SKYPIEA_MIN_DWELL_S", "5")),
        max_per_tool=int(e("SKYPIEA_MAX_PER_TOOL", "3")),
        max_total=int(e("SKYPIEA_MAX_TOTAL", "20")),
        always_warm=e("SKYPIEA_ALWAYS_WARM", "false").lower() == "true",
    )


def _server_of(labels: dict) -> str | None:
    return labels.get("skypiea.io/tool")


@kopf.on.startup()
async def startup(settings: kopf.OperatorSettings, **_) -> None:
    global _svc
    settings.posting.level = logging.WARNING
    settings.watching.server_timeout = 300
    settings.scanning.disabled = True   # no cluster-wide namespace/CRD discovery (namespaced RBAC)
    _svc = ControllerService(K8sScaler(NS), _cfg())
    runner = web.AppRunner(_svc.web_app(), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("SKYPIEA_API_PORT", "8081"))).start()

    # rebuild from live state BEFORE reconciling
    from kubernetes import client

    apps = client.AppsV1Api()
    deps = await asyncio.to_thread(apps.list_namespaced_deployment, NS,
                                   label_selector="skypiea.io/tier")
    for d in deps.items:
        server = _server_of(d.metadata.labels or {})
        if server in _svc.pool.s:
            _svc.pool.rebuild(server, d.spec.replicas or 0, d.status.ready_replicas or 0,
                              d.metadata.annotations or {}, time.time(), _svc.clock())
    log.info("rebuilt pool from %d deployments", len(deps.items))
    _svc.ready_event.set()
    asyncio.get_running_loop().create_task(_svc.run())


@kopf.on.event("apps", "v1", "deployments", labels=TOOL_SELECTOR)
async def on_deployment(body, **_) -> None:
    if _svc is None:
        return
    server = _server_of(body.get("metadata", {}).get("labels", {}))
    if server in _svc.pool.s:
        _svc.pool.observe(server, int(body.get("spec", {}).get("replicas") or 0),
                          int(body.get("status", {}).get("readyReplicas") or 0), _svc.clock())
