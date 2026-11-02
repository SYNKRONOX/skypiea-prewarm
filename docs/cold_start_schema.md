# `configs/cold_start.yaml` schema (v1)

Measured cold-start times per MCP tool server, read by the simulator. **Generated** from the E0
runs CSVs by `python -m experiments.e0_write_cold_start --env <kind|aws>`; never edit by hand.

> While `environment: kind`, every number is a **lower bound** (kind pulls images from a
> registry container on the same machine). The AWS run replaces it: regenerate with
> `--env aws`. Check `environment` before using the numbers as results.

```yaml
schema_version: 1
environment: kind            # kind | aws
status: "lower bound; replace with AWS ..."
units: ms
definition: total = scale request (0 -> 1 replica) -> first successful MCP tool call
source:                      # provenance: where every number came from
  experiment: e0_kind
  data_commit: f72afa0       # commit the E0 run was made at
  report_commit: 620a6ea     # commit whose code derived the phases
  images: 8ddcf55            # tool image tag (digests are in the runs CSV)
  runs_csv: results/e0_kind_runs.csv
  run_params: "n=10 modes=cached,uncached ..."
fallback: generic_fallback   # server for any class not listed below
fallback_patterns: ['^other(_.*)?$']
servers:
  <server>:                  # one per server in tools/tool_map.yaml
    tier: light | medium | heavy
    classes: [<tool class>, ...]      # the simulator's tool names map to servers through this
    cached:                  # image already on the node
      n: 10
      median_ms: 2241.5      # total
      p95_ms: 2767.2
      phases_median_ms:      # medians of each phase (they need not sum to median_ms)
        scheduling: ...      # scale request -> pod scheduled
        image_pull: ...      # 0 when cached; includes queueing behind other pulls
        container_start: ... # sandbox, network, image unpack, container create
        app_init: ...        # process start -> /healthz answers (imports, data/model load)
        readiness_lag: ...   # /healthz OK -> pod Ready (readiness probe period 1 s)
        first_call: ...      # Ready -> first successful MCP call through the Service
    uncached: {...}          # every tool image deleted from the node first: full pull
    warm_call_median_ms: 18.2   # MCP call to an already-Ready pod (service_ms = 0)
parallel_3:                  # optional: 3 different tools scaled up at the same moment
  note: ...
  runs_csv: results/e0_kind_p3_runs.csv
  run_params: ...
  servers:
    <server>: {cached: {n, median_ms, p95_ms}, uncached: {n, median_ms, p95_ms}}
```

How the simulator should use it:

- Tool class -> server: the server whose `classes` lists it; otherwise `fallback` (also for
  any class matching `fallback_patterns`).
- A cold call costs `cached` (image on the node, the normal case once a tool has run there)
  or `uncached` (first ever start on a node). Use `median_ms`, or sample between median and
  p95; the full per-run distribution is in `source.runs_csv`.
- A warm call costs `warm_call_median_ms` plus the tool's own service time from the trace.
- When several tools are warmed at once, `parallel_3` shows the contention cost.
