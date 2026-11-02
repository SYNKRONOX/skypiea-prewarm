"""Ready-pod counts per server. ReadyTracker is fed by an EndpointSlice watch in the cluster
(K8sEndpointWatcher) or directly by tests."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import defaultdict

log = logging.getLogger("skypiea.gateway.readiness")


class ReadyTracker:
    def __init__(self) -> None:
        self._ready: dict[str, int] = defaultdict(int)
        self._waiters: dict[str, list[asyncio.Future]] = defaultdict(list)

    def ready(self, server: str) -> int:
        return self._ready[server]

    def set_ready(self, server: str, n: int) -> None:
        self._ready[server] = n
        if n > 0:
            for f in self._waiters.pop(server, []):
                if not f.done():
                    f.set_result(True)

    async def wait_ready(self, server: str, timeout_s: float) -> bool:
        if self._ready[server] > 0:
            return True
        if timeout_s <= 0:
            return False
        fut = asyncio.get_running_loop().create_future()
        self._waiters[server].append(fut)
        try:
            return await asyncio.wait_for(fut, timeout_s)
        except TimeoutError:
            return False
        finally:
            if fut in self._waiters.get(server, []):
                self._waiters[server].remove(fut)


class K8sEndpointWatcher:
    """Watches EndpointSlices in the namespace (read-only RBAC) and feeds ReadyTracker with the
    number of ready endpoints per tool Service. Runs the blocking watch in a thread."""

    def __init__(self, tracker: ReadyTracker, namespace: str, service_to_server: dict[str, str],
                 loop: asyncio.AbstractEventLoop):
        self.tracker, self.ns, self.svc2srv, self.loop = tracker, namespace, service_to_server, loop
        self._slices: dict[str, tuple[str, int]] = {}
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="endpointslice-watch").start()

    def stop(self) -> None:
        self._stop.set()

    def _publish(self, svc: str) -> None:
        server = self.svc2srv.get(svc)
        if server is None:
            return
        n = sum(c for s, c in self._slices.values() if s == svc)
        self.loop.call_soon_threadsafe(self.tracker.set_ready, server, n)

    def _run(self) -> None:
        from kubernetes import client, config, watch

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        api = client.DiscoveryV1Api()
        while not self._stop.is_set():
            try:
                w = watch.Watch()
                for ev in w.stream(api.list_namespaced_endpoint_slice, self.ns,
                                   timeout_seconds=300):
                    sl = ev["object"]
                    svc = (sl.metadata.labels or {}).get("kubernetes.io/service-name", "")
                    if ev["type"] == "DELETED":
                        self._slices.pop(sl.metadata.name, None)
                    else:
                        ready = sum(1 for e in sl.endpoints or []
                                    if e.conditions is None or e.conditions.ready is not False)
                        self._slices[sl.metadata.name] = (svc, ready)
                    self._publish(svc)
                    if self._stop.is_set():
                        break
            except Exception as e:  # reconnect on any watch error
                log.warning("endpointslice watch error: %s; retrying", e)
                self._stop.wait(1.0)
