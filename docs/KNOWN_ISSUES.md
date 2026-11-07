# Known issues and workarounds

One entry per known limitation, in the same order as README
["Known limitations"](../README.md#known-limitations), with what to do about it. If you hit one
of these, it is already known: no need to report it again (but do report if the workaround does
not work). New problems: [`TRIAGE.md`](TRIAGE.md). Security: [`../SECURITY.md`](../SECURITY.md).
What the results do not show, in plain language: [`limits.md`](limits.md).

## Running it

| Issue | Workaround |
|---|---|
| **Tier 2 needs the v1.2 release asset.** `make fetch-models` downloads it; without network it stops with exit 2 and changes nothing. | Tiers 0 and 1 need no download. On a machine that already has the bundles: `BUNDLES_DIR=models make live`. |
| **The Live Console needs an LLM.** There is no scripted mode for the console (requested: `docs/handoff/REQUESTS_W2.md` R6). | Use Tier 1: `make agent-demo` sends the same kind of tool calls through the gateway with a scripted LLM. |
| **`ollama pull` takes one model per call.** `ollama pull a b` fails with "accepts 1 arg(s), received 2". | `ollama pull llama3.1:8b`, then (optional) `ollama pull qwen2.5:7b` and `ollama pull qwen3:8b`. |
| **The Live Console's LLM can claim actions it did not take.** | The truth check marks such claims ("Not done: ..."); the booking record shows the real state. Read the record, not the reply. |
| **Skye's 54/54 suite result was tuned on that suite.** The guardrails were developed while looking at llama3.1:8b's failures on the same 18 cases; temperature 0 and a fixed seed make reps nearly identical; before and after used different demo worlds. | Read it as "every suite case passes", not as a reliability figure. Try your own lines in the console; see `docs/SUITE_BEFORE_AFTER.md` for the per-model numbers. |
| **The Skypiea Air world is synthetic.** Only airport codes, city names and coordinates are real; the demo-world date is fixed (Monday 11 January 2027). | Say "tomorrow" or "next Friday" relative to 11 January 2027, not to today. `python -m agent.skyair.world` prints the world summary. |
| **The Live Console has no authentication** and does not check where a request comes from (R9). It binds to 127.0.0.1 only. | Do not expose port 8090 (no tunnel or proxy); close other browser tabs while presenting. Details: `SECURITY.md`. |
| **Only Linux x86-64 and Windows + WSL2 are tested.** macOS and arm64 are untested; the scripts use GNU `sha256sum`, `ss`, `setsid`. | On macOS or arm64, Tier 0 (Python 3.12, make, git) is the safe path; for Tier 1 use a Linux x86-64 VM or WSL2. Reports of what breaks are welcome. |
| **Python 3.12 is required** (`requirements.lock` is resolved for 3.12; `make doctor` names a wrong version). | Install 3.12 (`make doctor` prints the command for Ubuntu), or `make setup PYTHON=/path/to/python3.12`. |
| **`make live` uses fixed local ports** (8090, 3001, 18180-18182, 18280-18283) (requested: R7). | Stop whatever holds them (`ss -ltnp | grep -E ':(8090|3001|18180|18280) '`). `make demo` ports can be moved: `PF_BASE=28080 GRAFANA_PORT=3300 make demo`. |
| **`make demo-clean` and `make clean-cluster` delete the whole kind cluster**, including the Live Console's stacks if they run in it. | Use `make live-clean` to stop only the console. `make review` uses its own cluster (`skypiea-review`) and deletes only that one. |
| **A second `make demo-clean` says "removed registry"** although there was none (requested: R8). | Harmless. |
| **The gateway does not answer MCP `initialize`.** It implements the stateless MCP revision 2026-07-28; older clients that start with the `initialize` handshake get -32601 (R10). | Call `tools/list` and `tools/call` directly (`docs/REVIEWER_GUIDE.md`, "Use Skypiea yourself"), or use the repo's agent (`make agent-demo`). |
| **Docker stopped, or too little memory.** Pods `Pending`/`OOMKilled`. | Start Docker; give it at least 6 GB (WSL2: `memory=8GB` under `[wsl2]` in `%UserProfile%\.wslconfig`, then `wsl --shutdown`). `make doctor` checks both. |
| **CI does not cache Docker layers**: the `e2e` job builds every image from scratch. | None needed; it is slower, not wrong. |

## Reading the results

| Issue | What to do |
|---|---|
| **The AWS scenario runs are small**: 20 sessions per policy, so several CIs overlap; idle pod-seconds is one counter per run with no CI. | Quote predictive vs keep-alive as "on the keep-alive curve", not "better". The CIs are in `results/scenario_aws_summary.csv`. |
| **Cold starts come from one 2-vCPU node.** Pricing is an assumption; kind numbers are a lower bound. | Do not quote dollar or production latency figures. AWS timings cannot be reproduced without your own account (Tier 3). |
| **The simulator gives each session its own pods and its own node**; the 2-at-once start time is borrowed from the 3-at-once measurement. | Read the simulator margins as favourable to prediction vs keep-alive (`docs/limits.md`). |
| **The shipped coding profile was adopted after its TEST margins were seen.** | The frozen profile's rows are kept next to it in every table: compare both. |
| **tau2 is small** (56 TEST sessions with timing); its users are LLM-simulated; tau-bench has no timestamps. | Treat support-profile numbers as indicative; the ranker does not beat the n-gram on tau2. |
| **The `prd` profile and every SYNTHETIC row are synthetic.** | They show the mechanism runs; they say nothing about real workloads. |
| **Everything on kind is a demo run** (1 to 10 conversations per cell; Skypiea always idled more than the baseline in the console). | Not results. LLM conversations are not reproducible exactly, even at temperature 0 with a fixed seed (model and Ollama versions differ). |
| **Nobody independent has reviewed these results.** | `docs/REVIEWER_GUIDE.md` is the shortest way to check them yourself. |
