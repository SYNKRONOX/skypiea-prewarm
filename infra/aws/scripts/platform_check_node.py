"""Run ON the node (as root, via node-run.sh): Prometheus targets + Skypiea metrics, Grafana
health and every dashboard panel query, all through the API-server service proxy (no
port-forward). Functional check only. stdlib only."""
import json
import subprocess
import sys
import urllib.parse

NS = sys.argv[1] if len(sys.argv) > 1 else "skypiea-tools"
def raw(svc, path):
    p = subprocess.run(["k3s", "kubectl", "get", "--raw",
                        f"/api/v1/namespaces/{NS}/services/{svc}/proxy{path}"],
                       capture_output=True, text=True, timeout=20)
    if p.returncode:
        raise SystemExit(f"{svc}{path}: {p.stderr.strip()[:200]}")
    return json.loads(p.stdout)
def q(expr):
    path = "/api/v1/query?query=" + urllib.parse.quote(expr)
    return raw("skypiea-prometheus:9090", path)["data"]["result"]
t = raw("skypiea-prometheus:9090", "/api/v1/targets?state=active")["data"]["activeTargets"]
up = {}
for x in t:
    name = x["labels"].get("pod", x["labels"].get("job", "?")).rsplit("-", 2)[0]
    up.setdefault(name, []).append(x["health"])
print("prometheus targets:", ", ".join(f"{k}={','.join(v)}" for k, v in sorted(up.items())))
for m in ("skypiea_scale_events_total", "skypiea_warm_requests_total", "skypiea_warm_pool_size",
          "skypiea_scaleups_queued_total", "skypiea_scaleup_queue_depth"):
    r = q(f"sum({m})")
    print(f"  sum({m}) = {r[0]['value'][1] if r else 'no data'}")
h = raw("skypiea-grafana:3000", "/api/health")
print("grafana health:", h.get("database"), h.get("version"))
d = raw("skypiea-grafana:3000", "/api/dashboards/uid/skypiea-overview")["dashboard"]
ok = 0
for p in d["panels"]:
    exprs = [tg.get("expr") for tg in p.get("targets", []) if tg.get("expr")]
    res = [len(q(e.replace("$__rate_interval", "1m").replace("$__range", "1h"))) for e in exprs]
    ok += any(res)
    print(f"  panel {p.get('title')!r}: {len(exprs)} queries, series {res}")
print(f"dashboard {d['title']!r}: {ok}/{len(d['panels'])} panels return data")
