# Predictor contract (draft v1)

The gateway asks the predictor what to warm after **every tool result**. The production
predictor is an HTTP service (from the model branch); the simulator and tests call the same
`decide()` in-process. This file is the contract both sides code against. The gateway's
implementation and validator are `gateway/contract.py` and `gateway/predictor.py`.

## Request: `POST /decide`

```json
{
  "session_id": "3f2a...",
  "now_ms": 41250.0,
  "history": [
    {"tool_class": "order_lookup", "t_start_ms": 0.0,     "t_end_ms": 2140.5, "error": false, "batch_id": 0},
    {"tool_class": "policy_check", "t_start_ms": 38900.0, "t_end_ms": 41250.0, "error": false, "batch_id": 1}
  ],
  "oracle": [{"tool_class": "refund_issue", "in_ms": 1900.0}]
}
```

| Field | Meaning |
|---|---|
| `session_id` | Opaque id (the client's `Mcp-Session-Id`). Sessions are isolated. |
| `now_ms` | Milliseconds since the session's first call reached the gateway (end of the latest call). |
| `history` | All calls of this session so far, in arrival order. `tool_class` is the class from `tools/tool_map.yaml` (the predictor works on class names only). Times are on the same session clock as `now_ms`. |
| `error` | The call returned a JSON-RPC error or `isError: true`. |
| `batch_id` | Calls that overlapped in time (parallel tool calls) share a batch id; ids increase. |
| `oracle` | **Optional, oracle baseline only.** The replayer's true next calls, `in_ms` after `now_ms`. Real predictors must ignore it. |

Metadata only: no prompts, arguments or results are ever sent.

### Optional fields (v1.1, backward compatible): turn-start hint

| Field | Meaning |
|---|---|
| `trigger` | Absent = asked after a tool result (v1, unchanged: v1 requests never carry it). `"turn_start"` = a client's turn-start hint (`POST /v1/hint` on the gateway). |
| `turn` | Optional with `trigger`: the client's turn number (an integer). |

The gateway sends the hint request with the session's history so far, which is empty for a
new session. A v1 predictor ignores both fields (the model service's `parse_request` drops
unknown fields) and answers from the history. On an empty history the shipped `support` and
`coding` bundles answer with the first-tool distribution they learned from training (for
example support: user_lookup 0.50, reservation_lookup 0.41; see
`configs/live/predictor_probe.json`). The answer has the same shape and rules as below. The
gateway applies its decisions like any other (reason `hint:<model_version>` at the
controller).

The hint is **off by default**. The chart value is `platform.gateway.turnHint: false`, and
the env var `SKYPIEA_TURN_HINT=1` turns it on. While it is off, `POST /v1/hint` answers 404
and the gateway behaves exactly as before. The hint body must be exactly `{"turn": <int>}`
and the session id comes in the `Mcp-Session-Id` header. Any other field is refused (400), so
message text cannot reach the predictor. With the hint on, a call that a hint's decision
predicted carries `X-Skypiea-Warm-Source: hint` (or `call` when the latest decision came
after a tool result). The gateway counts hints in `skypiea_gateway_hints_total` and their warm
requests in `skypiea_gateway_hint_warms_total{tool}`. For a session that a hint opened, the
session clock restarts at its first call, so `t_*_ms` and `now_ms` are the same as without a
hint.

## Response

```json
{
  "ranked": [{"tool_class": "refund_issue", "p": 0.55}, {"tool_class": "END", "p": 0.45}],
  "decisions": [
    {"tool_class": "refund_issue", "warm": true, "copies": 1, "start_in_ms": 0, "ttl_s": null, "prob": 0.55}
  ],
  "model_version": "markov2+lgbm-2026-11-02",
  "latency_ms": 3.1
}
```

| Field | Rule |
|---|---|
| `ranked` | Next-tool distribution. Each `p` in [0, 1]; **sum <= 1** (FR-1; the rest is END or unknown). May include `END`. |
| `decisions` | What to warm. Probabilities **may sum above 1** (several branches can be warmed at once). |
| `decisions[].warm` | Boolean. `false` entries are allowed (logged) and do nothing. |
| `decisions[].copies` | Integer in [0, 10]. The controller still applies its per-tool and cluster caps. |
| `decisions[].start_in_ms` | Integer >= 0. The gateway waits this long before asking the controller (semi-Markov timing). |
| `decisions[].ttl_s` | Integer >= 0, or `null` -> controller default TTL (30 s). The pod is removed at TTL + 5 s after its last use (FR-3). Keep-alive baselines send their own T. |
| `decisions[].prob` | In [0, 1]; for logging and analysis. |
| `model_version` | Non-empty string; appears in the controller's `reason` (`predict:<model_version>`). |
| `latency_ms` | The predictor's own compute time. |

`tool_class` values are class names; the controller maps them to servers with
`tools/tool_map.yaml` (unknown classes go to the fallback server).

## Failure behaviour (FR-4)

The gateway calls `/decide` **off the critical path** (after the tool result has been returned
to the client) with a **300 ms timeout**. If the predictor is down, slow, returns non-JSON, or
returns anything that fails the rules above, the gateway:

- counts it in `skypiea_gateway_predictor_errors_total{reason}`,
- applies **no** decision from that answer (no partial use of a bad answer),
- behaves exactly like plain scale-to-zero for that call.

A tool call never fails because of the predictor.

## Implementations

| Where | What |
|---|---|
| `gateway.predictor.HttpPredictorClient` | Production: `POST <url>/decide`. |
| `gateway.predictor.InProcessPredictorClient` | Wraps any `decide(DecideRequest) -> dict` (simulator, tests). |
| `gateway.predictor.FakePredictor` | Scripted answers, delays and failures (tests). |
| `gateway/predictor_stub.py` | **Placeholder service** with stand-in policies for functional checks, until the model branch's `predictors/policies.py` and its service are merged. Not a predictor; its numbers are not results. |

After the merge the model branch's service replaces the placeholder: set
`platform.predictor.url` in the chart (or keep the placeholder Deployment and point it at the
shared `decide()`), with no gateway change.
