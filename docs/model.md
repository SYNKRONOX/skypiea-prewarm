# Prediction engine (branch feat/model)

Work in progress; the full write-up is Phase 12. This file holds the calibration and cap
decisions, the warm-decision rules (Phase 5), the offline-simulation definitions (Phase 6) and
the predictor service contract alignment.

## Calibration (decided 2026-11-03)

The ranker ships **raw LightGBM scores** (`configs/model/ranker.yaml`, `calibration: none`).
`calibration: isotonic` applies the VAL-fitted isotonic map instead; the map is always fitted
and saved (`calibrator.json`) so either mode can be served. `python -m predictors.phase4`
reports TEST-set ECE both ways from one fitted model (`results/p4_report.md`). VAL cannot
choose between them: the isotonic map is fitted on VAL, so its VAL ECE is in-sample (0.000).
On TEST, raw row ECE is .002-.004 on the real sources and isotonic makes it slightly worse
there; isotonic helps only on tau2 and synthetic A. Calibration never reorders candidates, so
top-1/top-3 are identical in both modes.

## Per-session cap (decided 2026-11-03)

`per_session_cap` stays at **3** (`configs/model/graph.yaml`). Measured on full TraceLab
native, fixed split and 5-fold GKF (`python -m predictors.cap_sweep`, `results/p4_cap.md`):
cap 3 vs no cap costs at most 0.9 pt top-1 (graph n-gram, fixed split only; 0.0 under GKF;
ranker 0.1 pt), below the 1-point rule.

## Warm decisions (Phase 5, `predictors/decide.py`)

Parameters: `configs/model/decide.yaml`; cold starts: the infra cold-start file, loaded by path
(`predictors/cold_start.py`; no cold-start number lives in code; tool classes map to servers
by the file's `classes` lists, unlisted classes to its `fallback`). Prices and per-tier CPU:
`configs/model/pricing.yaml` (assumptions).

- Probabilities: the fail-open ladder (`predictors/ladder.py`) ranker -> n-gram -> majority
  -> none; a rung that raises, returns invalid values or exceeds the 40 ms budget (checked on
  return) is skipped and `degraded=true`.
- Abstain (`mode="prior"`, no decisions): last tool unseen in train, or max in-next-batch
  probability < `min_conf` (0.3). All rungs failing: `mode="none"`. Both mean the cold path.
- Threshold: fixed `tau` (0.6), or cost-based tau_j = c_idle*E[idle] / (c_idle*E[idle] +
  C_cold) clamped to [0.2, 0.95], with E[idle] = the decision's TTL and C_cold = cold-start
  compute + `cold_latency_usd_per_s` x cold seconds. With the latency value at 0 (compute
  only) tau_j is close to TTL / (TTL + cold start), about 0.9 for a 30 s TTL.
- Copies: smallest n with P(count <= n | tool in batch) >= 0.9, capped at min(3, 10).
- Timing: start_in_ms = max(0, gap_p50 - cold_p95 - margin); TTL from Ready until the p90
  arrival, clamped to [5 s, 600 s]; 30 s without timing data.
- Per server (contract P7): copies max, start_in min, ttl max. Operator caps
  (`apply_caps`): 3 pods per server, 20 in total, an optional ramp budget; when a cap binds,
  pods go to the highest prob x cold-start ms.

## Offline simulation (Phase 6, `sim/offline.py`)

Definitions are in the module docstring; the key modelling choices are: pods are per (session,
server), never shared across sessions; decisions are made at the end of each batch (no
request before the first call); a call that finds its pod still starting waits for it (FR-9
hold); a decision's TTL holds a prewarmed pod until its first use, then the controller
default (30 s) applies; "hideable" = not in the first batch and think gap >= the server's
median cold start.

Results (`results/sim_report.md`, figures `results/sim_pareto.png`, `results/tau_curve.png`;
cold starts from the E0 kind file, a lower bound): **on TraceLab and Copilot the predictive
policy does not beat fixed keep-alive.** At tau 0.6 it adds 2.0 (TraceLab) / 1.7 (Copilot)
points of warm calls over keep-alive 30 s, for 43% / 9% more idle pod-seconds, while
keep-alive 60 s gets 66.3% warm on TraceLab at 29k idle pod-s per 1,000 calls against our
60.5% at 26k. At tau 0.3 it reaches 70.0% warm (TraceLab, cached) at 59k; keep-alive 300 s
gets 80.2% at 90k. Its points lie on or above (worse than) the keep-alive curve. 94% (TraceLab, cached) / 82% (Copilot) / 78% (tau2) of calls are
hideable; the oracle warms exactly those. Prewarm precision is low on TraceLab (7.5% of
prewarmed pods used at tau 0.6). The timing rule (start at p50, TTL to p90) loses to "start
now, 30 s TTL" on tau2; neither rule has been chosen yet, and the choice should be made on
VAL.

Open contract question for the infra session: the sim assumes a decision's `ttl_s` covers a
prewarmed pod only until its first use, after which the controller default (30 s) applies.
The contract does not say which TTL governs a pod after it has been used.

## Predictor service contract: alignment with docs/predictor_contract.md (draft v1)

The gateway codes against `docs/predictor_contract.md` (infra branch, commit 18a711f) and its
validator `gateway/contract.py`. The predictor service (Phase 9, not written yet) will conform
to it exactly: `POST /decide {session_id, now_ms, history[...]} -> {ranked, decisions,
model_version, latency_ms}`. The table lists every field where the original model-side design
(Phase 9 spec: `POST /predict`) differs, and what the service will do.

| # | Original model-side design | Contract v1 | Service will |
|---|---|---|---|
| 1 | `POST /predict` | `POST /decide` | serve `/decide` |
| 2 | request `events[]` | `history[]` | read `history` |
| 3 | event `tool` | `tool_class` | read `tool_class` |
| 4 | event `t_offset_ms` (when the LLM emitted the batch) | `t_start_ms` | use `t_start_ms` as `t_offset_ms` |
| 5 | event `service_ms` | `t_end_ms` | derive `service_ms = t_end_ms - t_start_ms` (clipped at 0) |
| 6 | (none) | `now_ms` | accept; validate as finite and >= 0; not used as a feature in v1 (see P5) |
| 7 | request `scenario` (selects the model) | absent | one model per deployment, chosen at startup (see P1) |
| 8 | request `node` (workflow node, optional) | absent | node = null, so learned edges only; real traces have no nodes anyway (see P1) |
| 9 | (none) | `oracle[]` (optional) | accept and ignore it |
| 10 | `session_id` must match `^[A-Za-z0-9_.-]{1,64}$` | opaque `Mcp-Session-Id` | see P2 |
| 11 | more than 1,000 events -> 413; more than 200 -> use the last 200, `truncated=true` | "all calls of this session so far", no limit | see P3 |
| 12 | ranked item `{tool, prob}` | `{tool_class, p}` | emit `{tool_class, p}` |
| 13 | end token `<end>` | `END` | map `<end>` -> `END` at the boundary |
| 14 | decision `{tool, copies, start_in_ms, ttl_ms, prob}` | `{tool_class, warm, copies, start_in_ms, ttl_s, prob}` | emit contract fields |
| 15 | (no `warm` flag; only positive decisions) | `warm: bool`; `false` entries allowed | emit `warm=true` for decisions; below-threshold candidates as `warm=false, copies=0` (logged by the gateway) |
| 16 | `copies >= 1`, capped by `max_copies_per_tool` | int in [0, 10] | cap at min(config cap, 10) |
| 17 | `start_in_ms` float | int >= 0 | round to int, clip at 0 |
| 18 | `ttl_ms` in [5,000, 600,000] | `ttl_s` int >= 0 or null (= controller default 30 s) | `ttl_s = ceil(ttl_ms / 1000)` (5..600); never null (see P4) |
| 19 | response `mode`, `degraded`, `truncated` | absent (the validator ignores unknown keys) | send them as extra fields; see P6 |
| 20 | abstain = `mode="prior"` + no decisions | not described | `decisions: []` (valid under the contract) |
| 21 | always HTTP 200 for well-formed JSON; 413 for oversize bodies | status codes not specified; gateway timeout 300 ms | keep this behaviour; the internal budget of 40 ms is well under 300 ms |
| 22 | `/reload`, `/rollback`, `/healthz`, `/metrics` | not in the contract | add them; they do not affect `/decide` |

### Proposed changes to the contract (for the infra owner; not applied)

- **P1. Scenario selection.** The contract has no `scenario`, so one predictor serves one
  scenario. Proposal: add an optional `scenario` string (`coding` | `support`). The service
  answers with `decisions: []` if no model is loaded for it. Alternatively, state in the
  contract that the chart deploys one predictor per scenario. An optional `node` would only
  matter for synthetic workflow traces; I do not propose adding it.
- **P2. `session_id` format.** The service is stateless and never needs the raw id, and an
  MCP session id can be any visible-ASCII string of any length. Proposal: the gateway sends
  an opaque hash (for example the first 32 hex characters of the id's sha256), and the contract
  fixes the format to `^[A-Za-z0-9_.-]{1,64}$`. Until then the service accepts any string up
  to 256 characters, never logs it, and rejects longer ones with 413.
- **P3. History length.** TraceLab sessions reach thousands of calls; the model reads only the
  last 200 events. Sending the full history on every call is O(n^2) bytes per session.
  Proposal: the gateway sends at most the last 200 calls, plus `history_total: int` (the
  number of calls so far, used for the `step` feature). Without this, the service accepts up
  to 5,000 items, uses the last 200, and sets `truncated: true`. A 256 KB body limit holds
  about 2,000 items, so the limit would be raised to 1 MB.
- **P4. TTL unit.** `ttl_s` as an integer loses sub-second precision. That is harmless, since
  the minimum TTL is 5 s. `null` means the controller's default of 30 s. The service always
  sends an explicit value so the logged TTL is the one used.
- **P5. `now_ms`.** The model's timing features are trained on the gap between batches.
  `now_ms` equals the end of the latest call, so it adds nothing over `t_end_ms`. The contract
  is fine as is; the service only validates it.
- **P6. Optional response fields.** Proposal: document `mode` (`ranker` | `ngram` |
  `majority` | `prior` | `none`), `degraded` (bool) and `truncated` (bool) as optional
  response fields. That lets the gateway count fallbacks without the service changing shape.
- **P7. Class vs server granularity.** Decisions are per tool class, but pods are per server;
  for example, `shell` serves six classes. Two classes on one server could both ask for warm
  pods. Proposal: the contract states that the controller merges decisions per server, taking
  the max of `copies`, the min of `start_in_ms` and the max of `ttl_s`. The predictor does not
  read `tools/tool_map.yaml`.
- **P8. `batch_id` semantics.** The contract defines a batch as calls that *overlapped in
  time* at the gateway. The training data defines it as calls the LLM *emitted together*.
  These usually agree, but a slow call overlapping the next batch's first call would merge two
  batches. Proposal: the gateway assigns batch ids by the client request that carried the
  calls when possible, and by overlap only as a fallback. The contract should say which one
  applies.

### Decisions on the proposals (user, 2026-11-03)

This list goes to the infra session as written; the infra window applies it to
`docs/predictor_contract.md` and the gateway. The service (Phase 9) implements the decided
form.

| # | Decision | What the contract will say |
|---|---|---|
| P1 | open (no decision yet) | unchanged; one predictor per deployment until decided |
| P2 | accept | the gateway sends `session_id` as an opaque hash (first 32 hex characters of the sha256 of the MCP session id); format `^[A-Za-z0-9_.-]{1,64}$` |
| P3 | accept | the gateway sends at most the last 200 calls plus `history_total: int` (calls so far) |
| P4 | no change | `ttl_s` stays an integer or null; the service always sends an explicit value |
| P5 | no change | `now_ms` stays; the service validates it and does not use it as a feature |
| P6 | accept | optional response fields `mode` (`ranker` \| `ngram` \| `majority` \| `prior` \| `none`), `degraded` (bool), `truncated` (bool) |
| P7 | accept | the controller merges decisions per server: `copies` = max, `ttl_s` = max, `start_in_ms` = min |
| P8 | accept, modified | batch ids come from an explicit `X-Skypiea-Batch` header when the client sends one; otherwise the gateway groups calls that overlapped in time. The contract states this order |
