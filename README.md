# Skypiea: Predictive Prewarming for Agentic Tool Calls over MCP on Kubernetes

**Skypiea predicts which MCP tools an AI agent will call next and starts their containers on
Kubernetes before the call arrives, cutting cold-start latency at a bounded warm-pool cost.**

> **Status:** work in progress (final evaluation 6 Nov 2026).

[![CI](https://github.com/SYNKRONOX/skypiea-prewarm/actions/workflows/ci.yml/badge.svg)](https://github.com/SYNKRONOX/skypiea-prewarm/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

AI agents call tools in recognisable patterns. A customer-service agent that looks up an
order usually checks the refund policy next. When each tool runs as a scale-to-zero container,
every first call pays a cold start of hundreds of milliseconds to several seconds. Skypiea
learns these patterns from tool-call metadata and warms the right pods just in time, then
scales them back to zero.

---

## Project details

| | |
|---|---|
| **Course** | DS252 Cloud Computing, Indian Institute of Science (IISc), Bengaluru |
| **Project** | Skypiea: Predictive Prewarming for Agentic Tool Calls over MCP on Kubernetes |
| **Final evaluation** | 6 Nov 2026 |

| Name | SR No. | Focus |
|---|---|---|
| G. Parthav | 25302 | Predictors, simulator |
| L. Mohnish | 25451 | Traces, replayer, data pipeline |
| Ch. Dhanunjaya | 25415 | Controller, shaper, tools, deployment, cloud |

We used AI coding assistants for parts of the code and documentation. The team reviewed,
tested and owns all of it.

---

## What Skypiea delivers

- **Metadata-only prediction.** Predictors see only tool names, timings and batch structure,
  never prompts or arguments.
- **One Markov graph, used six ways:**
  1. *Branching:* warm every likely next tool above a threshold, not just the top-1.
  2. *k-step lookahead:* the probability that a tool is needed within the next k steps.
  3. *Semi-Markov timing:* learned inter-call gaps decide *when* to start a pod, not just which one.
  4. *Parallel batch sets with copy counts:* warm a whole parallel batch, with as many copies as it needs.
  5. *Higher-order back-off:* order-n contexts back off to order 1, then to unigram.
  6. *Fleet-wide demand:* sum the per-session predictions into cluster-wide pod targets.
- **A LightGBM re-ranker** over Markov features, with probability calibration and a warm
  threshold derived from the cost of a cold start versus an idle pod. A **small GRU** serves as
  the neural check.
- **Parallel tool calls** are treated as first-class: a batch is a set, not a sequence.
- **Our own scale-to-zero controller.** A [kopf](https://kopf.readthedocs.io/) operator on plain
  Deployments (OpenFaaS CE cannot scale to zero), plus a **request shaper** that holds a call
  for up to **150 ms** when a warm pod is about to be ready.
- **Real workloads:** TraceLab, GitHub Copilot traces, tau-bench, Toolathlon and BFCL.
- **Six baselines and a Pareto plot** of cold-start rate against idle pod-seconds.

---

## Architecture

```mermaid
flowchart LR
    R[Replayer / agent] -->|POST /mcp| S[Gateway<br/>150 ms hold]
    S --> T[MCP tool pods<br/>0..N per tool]
    S -->|call metadata| P[Predictor service<br/>Markov + LightGBM]
    P -->|decisions| S
    S -->|POST /v1/warm| C[kopf controller]
    C -->|scale 0..N| T
    T --> M[Prometheus / Grafana]
    S --> M
    C --> M
    SIM[Simulator<br/>500 sessions] -.->|same Policy code| P
```

The replayer (or a real agent) sends sessions through the gateway, which forwards each call
to a ready pod of its tool (holding it up to 150 ms if a pod is about to be ready) and then
tells the predictor what happened. The gateway passes the predictor's warm decisions to our
own scale-to-zero controller, which scales the likely next tools up before their calls
arrive. The simulator runs the same policy code offline. Details:
[`docs/gateway_controller.md`](docs/gateway_controller.md).

---

## Repository layout

| Path | Contents | Owner |
|---|---|---|
| [`traces/`](traces/) | Trace schema, converters, pipeline and loader API | L. Mohnish |
| [`configs/tool_classes/`](configs/tool_classes/) | Raw tool name → tool class rules per source | L. Mohnish |
| [`replayer/`](replayer/) | Open-loop trace replayer through the gateway, per-call CSV | L. Mohnish, Ch. Dhanunjaya |
| [`data/`](data/) | Dataset notes and pipeline (data itself is not committed) | L. Mohnish |
| [`predictors/`](predictors/) | Predictor and policy interfaces, baselines, Markov, evaluation | G. Parthav |
| [`sim/`](sim/) | Discrete-event warm-pool simulator | G. Parthav |
| [`gateway/`](gateway/) | MCP gateway (one `/mcp` endpoint, 150 ms hold; the request shaper), predictor client and placeholder | Ch. Dhanunjaya |
| [`controller/`](controller/) | kopf scale-to-zero controller | Ch. Dhanunjaya |
| [`skypiea_cli/`](skypiea_cli/), [`bin/skypiea`](bin/skypiea) | `skypiea status / policy / trace / explain` | Ch. Dhanunjaya |
| [`tools/`](tools/) | 16 MCP tool servers and images; `tools/tool_map.yaml` (class -> server) | Ch. Dhanunjaya |
| [`charts/skypiea/`](charts/skypiea/) | Helm chart: tools, gateway, controller, predictor placeholder, Prometheus + Grafana | Ch. Dhanunjaya |
| [`deploy/`](deploy/) | kind cluster + local registry, platform image | Ch. Dhanunjaya |
| [`infra/aws/`](infra/aws/) | Terraform: EC2 k3s (SSM only, no inbound ports), ECR, S3 | Ch. Dhanunjaya |
| [`experiments/`](experiments/) | Experiment runner and YAML configs | All |
| [`results/`](results/), [`report/`](report/) | Experiment outputs and final report | All |
| [`tests/`](tests/) | Unit tests | All |
| [`docs/`](docs/) | Schema and experiment documentation | All |

---

## Experiments

| ID | What we measure |
|---|---|
| **E0** | Real cold starts, with the image cached and uncached |
| **E1** | Next-tool top-1 / top-3 accuracy at k = 1, 3, 5 on tau-bench and Toolathlon |
| **E2** | Whole-batch coverage for parallel calls on BFCL and TraceLab |
| **E3** | Pareto plot of cold-start rate against idle pod-seconds |
| **E4** | p50 / p99 latency CDFs |
| **E5** | 500-session spike and soak test |

**Baselines:**

1. **Scale-to-zero:** no prewarming.
2. **Always-warm:** every tool always has a pod.
3. **Fixed keep-alive:** 30 s to 10 min after last use.
4. **Hybrid histogram:** Shahrad et al., *Serverless in the Wild*, USENIX ATC 2020.
5. **SpecBox-style Markov:** top-1 first-order prewarming.
6. **Oracle:** perfect knowledge of the next calls.

Details are in [`docs/experiments.md`](docs/experiments.md).

---

## Setup guide

### 1. Prerequisites

- [Docker](https://docs.docker.com/get-docker/)
- [kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installation)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- Python 3.11 or newer
- `make`

### 2. Clone and install

```bash
git clone https://github.com/SYNKRONOX/skypiea-prewarm.git
cd skypiea-prewarm
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
make test
```

### 3. Get the data

Raw data goes in `data/raw/` and is never committed. Rebuild it with the downloader, which
checks sizes first and records sha256 hashes (details in [`data/README.md`](data/README.md)):

```bash
python data/download.py --list
python data/download.py --dataset tracelab
```

Tests do not need the raw data: they use the small samples committed in `data/samples/`.

### 4. Start a local cluster

Also needs [Helm](https://helm.sh/) and `pip install -r requirements-infra.txt`.

```bash
make build-images   # 16 tool images, E0 probe, platform image (one at a time)
make cluster        # kind: 1 control-plane + 1 worker + local registry localhost:5001
make deploy         # helm install skypiea: tools at 0 replicas, gateway, controller,
                    # predictor placeholder, Prometheus + Grafana
bin/skypiea --context kind-skypiea status
make kind-integration   # functional check: Scenario A + B through the gateway, every policy
```

### 4b. AWS (EC2 + k3s)

Read [`docs/aws_plan.md`](docs/aws_plan.md) first (resources, cost, security, teardown), then
follow [`infra/aws/README.md`](infra/aws/README.md). There is no automatic stop (it would break
E0): `make aws-apply` writes a calendar reminder, and `make aws-destroy` removes everything.

### 5. Run an experiment

```bash
make e1
```

---

## Quick reference

| Command | What it does |
|---|---|
| `make test` | Run unit tests (`pytest -q`) |
| `make lint` | Lint with `ruff check .` |
| `make check-sizes` | Fail if any git-tracked file is over 50 MB |
| `make install-hooks` | Install the pre-commit size check |
| `python data/download.py --dataset <name>` | Download a dataset into `data/raw/` |
| `python tools/inspect_dataset.py <name>` | Read-only overview of a raw dataset |
| `python tools/make_sample.py --all` | Rebuild the committed samples |
| `make cluster` | kind cluster (1 control-plane + 1 worker) + local registry |
| `make deploy` | `helm install skypiea charts/skypiea` on kind |
| `make deploy-kind-e0` | Tools + E0 probe only (no controller), for E0 |
| `make e0-kind` / `make e0-kind-parallel` | E0 cold-start measurement on kind |
| `make kind-integration` | Functional replay of Scenario A/B under every policy |
| `make grafana-kind` | Grafana on http://127.0.0.1:3000 |
| `bin/skypiea status` | Tools, replicas, warm pool, policy (`--context` / `--kubeconfig`) |
| `bin/skypiea trace <sid>` / `explain <sid>` | Calls of a session with cold/warm, and why |
| `python -m replayer.replay --config <yaml>` | Replay trace sessions through the gateway |
| `make data SOURCE=tracelab` | Convert one raw dataset into `data/processed/<source>/` |
| `make data-all` | Convert every downloaded dataset |
| `make e1` | Run E1 (predictor accuracy) |
| `make clean-cluster` | Delete the kind cluster and the local registry |
| `kubectl get pods -n skypiea-tools -w` | Watch tool pods scale up and down |

---

## Roadmap

- [x] Project skeleton: schema, policy interface, frequency and Markov predictors, tests, CI
- [x] Dataset downloader, converters, tool-class mapping, group splits and loader (TraceLab, Copilot, tau-bench, tau2-bench, BFCL)
- [ ] Toolathlon (gated; waiting for access and the instructor's answer)
- [x] E0 on kind (local registry, lower bound): median total cold start 2.5 s cached, 4.1 s uncached
- [ ] E0 on AWS (the reported numbers)
- [ ] Markov features, LightGBM re-ranker, GRU check
- [x] Gateway (shaper), controller, replayer, `skypiea` CLI, Grafana dashboard
- [ ] Baselines and simulator
- [ ] AWS k3s cloud run (Terraform planned: 50 to add; not applied)
- [ ] E1 to E5 and final report

---

## Documentation

- [Trace schema](docs/schema.md)
- [Experiments and baselines](docs/experiments.md)
- [Data](data/README.md)
- [Infrastructure, tools and E0](docs/infra.md)
- [Gateway and controller](docs/gateway_controller.md), [predictor contract](docs/predictor_contract.md)
- [Cold-start input for the simulator](docs/cold_start_schema.md)
- [AWS plan review](docs/aws_plan.md), [AWS cloud run](infra/aws/README.md)

## License

Code is released under the [MIT License](LICENSE). Datasets are not redistributed and keep
their own licences (see [`data/README.md`](data/README.md)).
