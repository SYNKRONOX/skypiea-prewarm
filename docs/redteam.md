# Red team: predictor service (Phase 10)

Suite: `tests/redteam/` (`make -f mk/model.mk redteam`), the persistence audit
`tools_audit/audit_no_persistence.py` (`make -f mk/model.mk audit`), plus
`tests/test_service.py` (contract, bundles, fuzzing) and `tests/test_graph.py` (poisoning).
The service is the one in `services/predictor/` as committed with this file; tests use a
bundle trained on the committed samples. Result column: what the run on 2026-11-04 showed.

## Input attacks (`tests/redteam/test_inputs.py`)

| Attack | Expected | Result |
|---|---|---|
| invalid JSON, empty body, bad UTF-8, scalars, `[]`, `{}` | 400 for malformed JSON; 200 with no decisions for valid JSON of the wrong shape | pass |
| wrong types, missing fields, extra fields, `__proto__` keys | 200, no decisions or repaired values; extra fields ignored and never echoed | pass |
| nested JSON bomb (50,000 levels), 5,000-deep history | 400 (recursion caught) or 413; never 500 | pass |
| 10 MB body; 100,000 history items; lying Content-Length | 413 before parsing | pass |
| empty, one-item, 1,000 identical history items | 200; last 200 used, `truncated: true` above 200 | pass |
| tool names: empty, 10 KB, unicode, NUL/control chars, SQL / shell / format-string / path-traversal strings, `__proto__`, unseen names, upper case | mapped to `<unk>` (or lower-cased if valid); 200; the raw name is never in the response | pass |
| numbers: NaN, +-Inf, negative, 1e300, numeric strings, null, bool, list (times, batch ids, `now_ms`) | clipped or zeroed and counted by field name; 200 | pass |
| decreasing / duplicate timestamps, out-of-order and duplicate batch ids | repaired deterministically (batch ids renumbered by runs in arrival order); 200 | pass |
| session id: empty, spaces, `/`, unicode, 10 KB, `../`, newline, `%00` | 200, no decisions, degraded; never echoed | pass |
| 10,000 unique session ids | peak RSS grows < 50 MB (no per-session state) | pass |
| unseen tools only, all-error history, empty history, 200-call history | 200; ladder falls back; bounded time | pass |
| hypothesis fuzz (150 random bodies, `tests/test_service.py`) | 200/400 only; schema holds | pass |

## Isolation, privacy, determinism (`tests/redteam/test_service_attacks.py`, audit)

| Attack / check | Expected | Result |
|---|---|---|
| two divergent sessions interleaved from 50 threads (400 calls) | identical to each answered alone | pass |
| same request 100 times | byte-identical answers (excluding `latency_ms`) | pass |
| 2,000 random requests | ranked sorted, sum <= 1 + 1e-9, all p in [0, 1]; warm copies in [1, cap], ttl_s in [5, 600]; warm=false has copies 0 | pass |
| marker in session ids and tool names, 200 sessions (audit) | marker absent from the working dir, new /tmp files, logs, /metrics, /healthz; /decide writes no files | pass |
| same data and seed | byte-identical bundle files (`test_bundle_round_trip_is_deterministic`) | pass |

## Reload and supply chain

| Attack | Expected | Result |
|---|---|---|
| reload x3 under 8 concurrent request threads | zero non-200 answers | pass |
| tampered byte, truncated file, missing file, wrong schema, wrong vocabulary, empty folder, 65 MB file, symlinked file, 200-char version | refused with a reason; old model keeps serving | pass |
| missing / wrong token; ADMIN_TOKEN unset | 401 / 401 / 403 | pass |
| URI outside MODEL_ROOT, `/etc`, `file:///etc`, `https://...`, `s3://` bucket not allowlisted, `..` traversal | 422 refused; old model serving | pass |
| malformed reload bodies | 400 | pass |
| double reload; rollback; 4 concurrent reloads | rollback restores the previous; concurrent reloads get 409 while one runs | pass |
| kill -9 during a reload, restart (audit) | serves the model at MODEL_PATH | pass |
| `WORKERS` > 1 | /reload and /rollback answer 409 (a reload would reach one worker only) | by construction |
| grep guard: pickle, joblib, eval, exec, os.system, shell=True in services/ and predictors/ (.py) | none | pass |
| poisoning: rebuilt counts from junk sessions (`tests/test_graph.py::test_rebuild_rejects_junk_and_keeps_top1`) | rejected; clean top-1 within 1 point | pass |

## Resource limits

| Check | Expected | Result |
|---|---|---|
| 200-call history | answered in < 2 s wall in tests; service latency_ms < 1 s | pass |
| ladder budget 40 ms per rung | a slow rung is discarded, next rung answers, `degraded: true` (`tests/test_decide.py`) | pass |
| load tests | see docs/model.md "Predictor service" (open-loop 75 / 150 req/s, closed loop 10 / 200) | see there |

## Known limits (not covered, or covered only partly)

1. No TLS and no authentication on /decide: the service sits behind the cluster network; only
   /reload and /rollback are protected (admin token).
2. The 40 ms budget is checked when a rung returns; Python cannot pre-empt a running rung, so
   one pathological call still costs its own time once.
3. Reload time-of-check vs time-of-use: files are read into memory once and hashed from those
   bytes, so a file swapped after the check cannot be served; but a folder changed while it is
   being read can make a reload fail (it then keeps the old model).
4. Metric labels are fixed sets (outcome, reason, field names, model version); the version
   label changes only on reload. A reload loop could still add one series per version.
5. Floating point: ranked sums are rounded to 6 decimals and corrected downwards if the
   rounded sum exceeds 1; consumers should still allow 1e-9.
6. `predictors/workflow.py` has an `lru_cache` (synthetic workflow parsing only); it is not on
   the service path and holds no session data.
7. With `WORKERS` > 1 each worker has its own model and metrics registry; /metrics shows one
   worker per scrape. Scale with replicas for production metrics and reload.
8. s3:// reload needs boto3, which is not in the image; s3 URIs are refused there.
9. Peak-RSS growth is used as the memory check (ru_maxrss), not a heap profile.
10. The fresh-eyes review (Phase 11) was not done in this pass.
