# Demo: the whole stack on a laptop (kind)

`make demo` runs Skypiea end to end on a local kind cluster, starting from a fresh clone. It
creates the cluster, builds the images, deploys the chart with predictor profile `prd`, replays
PRD Scenario A sessions under scale-to-zero and then under the predictor, and prints a table.
The cluster stays up afterwards so you can use the CLI, Grafana and the agent
([`docs/AGENT.md`](AGENT.md)).

> **This is a functional demo. Its numbers are not results.** The think gaps are shortened,
> cold starts come from kind on a laptop, and the sessions are SYNTHETIC (sampled from the PRD
> scenario graphs). For results, see the README and `docs/results_summary.md`.

## Prerequisites

These are the versions we tested with. Other recent versions should work too.

| Tool | Tested version | Notes |
|---|---|---|
| Docker | 29.9.2 (Docker Desktop 4.97.0, WSL2 backend) | give Docker **at least 6 GB of memory** (see Troubleshooting) |
| kind | v0.33.0 (node image v1.37.0) | |
| kubectl | v1.37.2 | |
| Helm | v4.3.1 | |
| Python | 3.12 | `make setup` creates `.venv` from `requirements.lock` |
| make, curl, git | any | |
| OS | Ubuntu 24.04 on WSL2 (kernel 6.18), 24 vCPU, 16 GB RAM laptop | any Linux or macOS with Docker should work |

You do not need datasets, cloud accounts or any API key. `make demo` builds the predictor
bundle from the samples committed in `data/samples/`.

## Commands

```bash
git clone https://github.com/SYNKRONOX/skypiea-prewarm.git && cd skypiea-prewarm
make setup          # .venv from requirements.lock (about 1-2 min with a pip cache)
make demo           # cluster, images, deploy, two replays, table (see "Time" below)
make demo-failures  # optional: stop the predictor, kill the controller; calls must still succeed
make agent-demo     # optional: the agent (scripted LLM) through the gateway, docs/AGENT.md
make demo-clean     # stop port-forwards, delete the cluster and registry, remove .demo/
```

`make demo` can be run again: it reuses the cluster and any images that already exist.

## What each step does, and what you should see

`demo/run.sh` prints `=== [<seconds since start>s] <step>` before each step.

| Step | What it does | Expected output |
|---|---|---|
| 1/8 | `deploy/kind/up.sh`: registry `localhost:5001` + kind cluster `skypiea` (1 control-plane + 1 worker) | `kubectl get nodes` table, both nodes `Ready` |
| 2/8 | `python -m demo.prepare`: SYNTHETIC PRD traces from `data/samples`, a `prd` predictor bundle (`models/lgbm-prd-demo-<sha>/`), and 8 Scenario A + 8 Scenario B sessions with think gaps capped at 6 s and tool time capped at 2 s (`.demo/sessions.json`) | `scenario A: 8 SYNTHETIC sessions, 19 calls ...`, `bundle models/lgbm-prd-demo-<sha>` |
| 3/8 | builds the 16 tool images and the platform image, one at a time, plus `skypiea/predictor:prd-demo-<hash>` (only images that are missing) | one line per image with its size |
| 4/8 | pushes the images to `localhost:5001` | `pushed 16 tools, platform, predictor` |
| 5/8 | `demo/deploy.sh` (helm): 16 tools at 0 replicas, gateway, operator, stub and real predictor (profile `prd`), Prometheus, Grafana. The gateway serves the **stub**. Then the loopback port-forwards start | `deployed: ... gateway serves the stub`, `port-forwards up: ...` |
| 6/8 | `gateway/scenario_run.py`: Scenario A, 8 sessions, 4 at once, policy `scale_to_zero` | one line per session, then `POLICY {...}` and `FAILURES: none` |
| 7/8 | helm switches the gateway to the **real predictor** (`serve=model`), then the same replay under `predictive` | `== A predictive: start ... 'profile': 'prd'`, `FAILURES: none` |
| 8/8 | `python -m demo.table`: the table below | the table |

The table from our run on 2026-11-05 (`.demo/demo_table.md`; functional demo, not results):

```
FUNCTIONAL DEMO, NOT RESULTS: kind cold starts (laptop), SYNTHETIC PRD sessions (profile prd), shortened think gaps

| Scenario | Policy | sessions | calls | ok | cold rate | added p50 ms | added p99 ms | idle pod-s |
|---|---|---|---|---|---|---|---|---|
| A | scale_to_zero | 8 | 19 | 19 | 94.7% | 2,904 | 5,682 | 47 |
| A | predictive | 8 | 19 | 19 | 21.1% | 0 | 3,269 | 105 |
```

You should get the same shape: every call `ok`; under `scale_to_zero` almost every call is
cold, and each one adds a kind cold start (about 2-4 s). Under `predictive` most calls after
the first one in a session find a warm pod, at the cost of more idle pod-seconds. Your exact
numbers will differ from run to run.

When it finishes, the port-forwards are still running, and they listen on 127.0.0.1 only:

```bash
bin/skypiea --context kind-skypiea status     # CLI: tools, replicas, policy
# Grafana:  http://127.0.0.1:3000            (anonymous viewer; "Skypiea" dashboard)
# Gateway:  http://127.0.0.1:18080/mcp       (MCP endpoint used by make agent-demo)
make demo-portforward                         # restart the port-forwards if they died
```

## Time, disk and memory (measured on the laptop above)

| What | Measured |
|---|---|
| `make demo`, cluster already up, images already built | 243 s (4 min 3 s): deploy about 55 s, each replay about 50-65 s, the switch to the model and draining back to 0 about 60 s |
| `make demo` from a fresh clone (cluster deleted first; Docker build cache warm) | see "Fresh-clone check" below |
| Building the images on a machine with no Docker cache | not measured here (this laptop's build cache was warm). Expect several minutes for each image tier, mostly `pip install` |
| Docker memory, kind nodes | control-plane 0.7 GiB, worker 1.1 GiB while the agent ran (a few tools up); registry 24 MiB |
| Image sizes (uncompressed, as `docker images` shows them) | light tools 265 MB (9 of them), medium 470 MB (4), heavy 625-634 MB (3), platform 400 MB, predictor 528 MB, `kindest/node` 1.34 GB, `registry:3` 87 MB. The images share their Python base layers, so the disk they take together is much less than the sum |
| Disk inside the kind worker (containerd image store) | 3.5 GB, including older image tags from earlier work |

Plan for **about 15 GB of free disk** and **6 GB of memory for Docker**.

## Failure injection

```bash
make demo-failures
```

`demo/failure_injection.sh` sends tool calls through the gateway (`python -m agent.probe`)
while one part of the platform is down. It then restores the stack:

1. **Predictor stopped**: `skypiea-model` and `skypiea-predictor` are scaled to 0. The gateway
   gets no decisions, so it falls back to plain scale-to-zero (the cold path). Every call must
   still succeed; the calls are simply cold.
2. **Controller killed**: the controller pod is deleted, and its Deployment starts a new one.
   The new controller rebuilds its state from the Deployment annotations. Calls made in the
   meantime must still succeed.

The result from our run is in the "Fresh-clone check" section below.

## Troubleshooting

- **Docker memory on WSL2.** Docker Desktop limits the memory of its VM. If tool pods stay
  `Pending` or get `OOMKilled`, or Grafana restarts, raise the limit: Docker Desktop >
  Settings > Resources (or `memory=8GB` under `[wsl2]` in `%UserProfile%\.wslconfig`, then
  `wsl --shutdown`). Check it with `docker info --format '{{.MemTotal}}'`.
- **`kubectl` says `Unable to connect to the server: EOF` after the laptop slept or Docker
  Desktop restarted.** Docker Desktop's port forward to the kind API server can go stale. Run
  `docker restart skypiea-control-plane skypiea-worker`; the cluster comes back with its state.
  We hit this on 2026-11-05.
- **Ports in use.** The demo uses 5001 (registry), 18080-18083 (gateway, controller, stub,
  model) and 3000 (Grafana), all on 127.0.0.1. Find what holds a port with
  `ss -ltnp | grep :18080`. Change the gateway port with `GW_PORT=...`, or start without
  Grafana: `GRAFANA=0 demo/portforward.sh start`.
- **Port-forwards die.** `kubectl port-forward` stops when its pod is replaced (for example
  after a helm upgrade). Run `make demo-portforward`.
- **Pull secrets / `ErrImagePull`.** kind pulls from the local registry `localhost:5001`, so
  no pull secret is needed (`values-kind.yaml` sets `imagePullSecrets: []`). If pods show
  `ErrImagePull`, check that the registry runs (`docker ps | grep kind-registry`) and that
  it is connected to the `kind` network (`deploy/kind/up.sh` does both; you can run it again).
  For a private remote registry, create a `docker-registry` Secret in `skypiea-tools` and set
  `imagePullSecrets`.
- **Grafana evicted in a loop** (older checkouts): Grafana 13 downloaded about 540 MB of
  plugins into a 512 Mi volume. The chart now sets `GF_PLUGINS_PREINSTALL_DISABLED=true`.
  Clean up with `kubectl -n skypiea-tools delete pods -l skypiea.io/tool=skypiea-grafana
  --field-selector=status.phase!=Running`.
- **Ollama on Windows, cluster and agent in WSL2** (for `make agent-demo LLM=openai-compatible`):
  - With WSL **mirrored networking** (`networkingMode=mirrored` in `.wslconfig`), Windows'
    `localhost:11434` is also `localhost:11434` in WSL. Check it with
    `curl http://localhost:11434/v1/models`.
  - With the default **NAT networking**, use the Windows host IP instead:
    `export AGENT_BASE_URL=http://$(ip route show default | awk '{print $3}'):11434/v1`. You
    also need Ollama to listen on all interfaces: set the user environment variable
    `OLLAMA_HOST=0.0.0.0` on Windows, then restart Ollama, and allow port 11434 through the
    Windows firewall.
  - The agent runs in WSL, not inside the cluster, so pods never need to reach Ollama.
- **`helm` prints `client-side throttling` lines.** These are harmless.
- **Start over:** `make demo-clean && make demo`.

## Fresh-clone check

(Filled in after the verification run; see `docs/handoff/AGENT_REPORT.md`.)
