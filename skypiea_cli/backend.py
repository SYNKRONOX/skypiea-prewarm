"""Access to the Skypiea services through the Kubernetes API server, using a kubeconfig.

Calls go through the API server's service/pod proxy (``/api/v1/namespaces/<ns>/services/
<svc>:<port>/proxy/<path>``), so the CLI needs nothing but the kubeconfig the user already has
(kind context or ~/.kube/skypiea-aws): no port-forward, no extra credentials, nothing stored.

Session introspection (``sessions``, ``trace``, ``explain``) needs the gateway's admin token
(issue #7): ``SKYPIEA_ADMIN_TOKEN`` if set, else read with the same kubeconfig from Secret
``skypiea-admin`` (key ``admin-token``) that the chart creates. It is sent in X-Admin-Token
and never printed or stored.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import time
from contextlib import contextmanager
from typing import Any, Protocol

DEFAULT_NS = "skypiea-tools"
ADMIN_SECRET, ADMIN_KEY = "skypiea-admin", "admin-token"


class AdminTokenError(RuntimeError):
    """The gateway refused session introspection (no token, wrong token, or disabled)."""


class Backend(Protocol):
    namespace: str

    def deployments(self) -> list[dict[str, Any]]: ...
    def controller_state(self) -> dict[str, Any]: ...
    def get_policy(self) -> dict[str, Any]: ...
    def set_policy(self, name: str, params: dict[str, Any]) -> dict[str, Any]: ...
    def session(self, sid: str) -> dict[str, Any] | None: ...
    def sessions(self) -> list[dict[str, Any]]: ...


class KubeBackend:
    def __init__(self, kubeconfig: str | None = None, context: str | None = None,
                 namespace: str = DEFAULT_NS):
        from kubernetes import client, config

        self.kubeconfig = os.path.expanduser(kubeconfig) if kubeconfig else None
        self.context, self.namespace = context, namespace
        api_client = config.new_client_from_config(config_file=self.kubeconfig,
                                                   context=context)
        self.core = client.CoreV1Api(api_client)
        self.apps = client.AppsV1Api(api_client)
        self.api = api_client
        self._token: str | None = None

    def admin_token(self) -> str | None:
        """SKYPIEA_ADMIN_TOKEN, else Secret skypiea-admin/admin-token in the namespace."""
        if self._token is None:
            tok = os.environ.get("SKYPIEA_ADMIN_TOKEN") or None
            if tok is None:
                try:
                    sec = self.core.read_namespaced_secret(ADMIN_SECRET, self.namespace)
                    raw = (sec.data or {}).get(ADMIN_KEY)
                    tok = base64.b64decode(raw).decode() if raw else None
                except Exception:  # no Secret, or no RBAC to read it
                    tok = None
            self._token = tok or ""
        return self._token or None

    # ---- raw proxy ----
    def _proxy(self, kind: str, name: str, port: int, path: str, method: str = "GET",
               body: Any = None, extra: dict[str, str] | None = None) -> tuple[int, Any]:
        url = f"/api/v1/namespaces/{self.namespace}/{kind}/{name}:{port}/proxy/{path.lstrip('/')}"
        # kubernetes-client >= 33: serialize (adds host + auth from the kubeconfig), then send
        method_, full_url, headers, body_, post = self.api.param_serialize(
            method, url, header_params={"Content-Type": "application/json",
                                        "Accept": "application/json", **(extra or {})},
            body=body, auth_settings=["BearerToken"])
        try:
            resp = self.api.call_api(method_, full_url, header_params=headers, body=body_,
                                     post_params=post, _request_timeout=15)
            resp.read()
            status, raw = resp.status, resp.data
        except Exception as e:  # network/TLS errors -> report, never crash the CLI
            return 599, {"error": f"{type(e).__name__}: {e}"[:200]}
        try:
            data = json.loads(raw or b"null")
        except ValueError:
            data = {"error": (raw or b"")[:200].decode(errors="replace")}
        return status, data

    def _svc(self, name: str, port: int, path: str, method: str = "GET", body: Any = None):
        status, data = self._proxy("services", name, port, path, method, body)
        if status >= 400:
            raise RuntimeError(f"{name}{path}: HTTP {status}: {data}")
        return data

    # ---- Backend ----
    def deployments(self) -> list[dict[str, Any]]:
        out = []
        for d in self.apps.list_namespaced_deployment(
                self.namespace, label_selector="app.kubernetes.io/part-of=skypiea").items:
            labels = d.metadata.labels or {}
            out.append({"name": d.metadata.name, "tool": labels.get("skypiea.io/tool"),
                        "tier": labels.get("skypiea.io/tier"), "replicas": d.spec.replicas or 0,
                        "ready": d.status.ready_replicas or 0,
                        "image": d.spec.template.spec.containers[0].image})
        return out

    def controller_state(self) -> dict[str, Any]:
        return self._svc("skypiea-controller", 8081, "/v1/state")

    def get_policy(self) -> dict[str, Any]:
        return self._svc("skypiea-predictor", 80, "/admin/policy")

    def gateway_predictor_url(self) -> str | None:
        """SKYPIEA_PREDICTOR_URL of the gateway Deployment ("" = none; None = unknown)."""
        try:
            d = self.apps.read_namespaced_deployment("skypiea-gateway", self.namespace)
        except Exception:  # no gateway or no RBAC: unknown, fall back to the stub's view
            return None
        for e in d.spec.template.spec.containers[0].env or []:
            if e.name == "SKYPIEA_PREDICTOR_URL":
                return e.value or ""
        return ""

    def set_policy(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        tok = self.admin_token()           # issue #27: the stub's POST needs the admin token
        status, data = self._proxy("services", "skypiea-predictor", 80, "/admin/policy", "POST",
                                   {"name": name, "params": params},
                                   extra={"X-Admin-Token": tok} if tok else None)
        if status in (401, 403):
            raise AdminTokenError(
                f"predictor refused policy set (HTTP {status}): it needs the admin token. Set "
                f"SKYPIEA_ADMIN_TOKEN, or let the CLI read Secret {ADMIN_SECRET} in "
                f"{self.namespace}.")
        if status >= 400:
            raise RuntimeError(f"skypiea-predictor/admin/policy: HTTP {status}: {data}")
        return data

    def _gateway_pods(self) -> list[str]:
        pods = self.core.list_namespaced_pod(self.namespace,
                                             label_selector="skypiea.io/tool=skypiea-gateway")
        return [p.metadata.name for p in pods.items if p.status.phase == "Running"]

    def _admin_get(self, pod: str, path: str) -> tuple[int, Any]:
        tok = self.admin_token()
        status, data = self._proxy("pods", pod, 8080, path,
                                   extra={"X-Admin-Token": tok} if tok else None)
        if status in (401, 403):
            raise AdminTokenError(
                f"gateway {pod} refused {path.split('/')[2]} (HTTP {status}): session "
                f"introspection needs the admin token. Set SKYPIEA_ADMIN_TOKEN, or let the CLI "
                f"read Secret {ADMIN_SECRET} in {self.namespace} (kubectl get secret "
                f"{ADMIN_SECRET} must work with this kubeconfig).")
        return status, data

    def session(self, sid: str) -> dict[str, Any] | None:
        # sessions live in one gateway replica: ask every gateway pod
        for pod in self._gateway_pods():
            status, data = self._admin_get(pod, f"/v1/sessions/{sid}")
            if status == 200:
                return {**data, "gateway_pod": pod}
        return None

    def sessions(self) -> list[dict[str, Any]]:
        out = []
        for pod in self._gateway_pods():
            status, data = self._admin_get(pod, "/v1/sessions")
            if status == 200:
                out += [{**s, "gateway_pod": pod} for s in data["sessions"]]
        return out


@contextmanager
def port_forward(kube: KubeBackend, svc: str, remote: int):
    """kubectl port-forward with the same kubeconfig/context (for bulk traffic: the replayer)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    cmd = ["kubectl", "-n", kube.namespace, "port-forward", f"svc/{svc}", f"{port}:{remote}"]
    if kube.kubeconfig:
        cmd[1:1] = ["--kubeconfig", kube.kubeconfig]
    if kube.context:
        cmd[1:1] = ["--context", kube.context]
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        yield f"http://127.0.0.1:{port}"
    finally:
        p.terminate()
        p.wait(timeout=5)
