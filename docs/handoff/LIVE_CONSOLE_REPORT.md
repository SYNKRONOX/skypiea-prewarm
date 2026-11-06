# Live Console report (branch feat/live-console)

Branch `feat/live-console`, created from `feat/agent` in `~/skypiea-infra`. **Nothing was
pushed or merged.** No AWS was used. No package was added; Playwright is not installed, see
"Not done". No key was read, printed or committed; Ollama needs none. **Both stacks and the
console are running**, started from `~/skypiea-infra` (`make live`), demo airline / profile
support, hint off, mirror concurrent, baseline scale-to-zero. The conversation was reset.

## What works (verified on 2026-11-06)

| Step | Status | Evidence |
|---|---|---|
| 1 vocabulary | done | `python -m agent.vocab`: all 11 console tools map to classes in the **real** profiles' vocabularies (support `lgbm-support-aabdfd2`, coding `lgbm-coding-af00023`, read from image `af000231bc7b`). No SYNTHETIC profile is used. Coverage table and abstain points: `docs/LIVE_CONSOLE.md` section 1 |
| 2 two stacks | done | `skypiea-base` and `skypiea-pred` in one kind cluster, shared images, namespace-scoped controllers. Tool images are pre-pulled, so every start is a cached start |
| 3 mirrored execution | done | Every call goes to both gateways with the same arguments and session id. The conversation runs on the predictive result, and results are compared (all matched in the runs). The baseline repeats each call with the **same gap**: a gap-matched lane, with per-call `base_gap_ms` / `pred_gap_ms`. Concurrent and sequential modes. Start contention is recorded per call |
| 3b turn-start hint | done, off by default | Gateway `POST /v1/hint` behind `platform.gateway.turnHint` (default false: 404) and contract v1.1 optional `trigger`/`turn`. The real predictor service answers it unchanged. Tests: `tests/test_gateway_hint.py`, `tests/test_service_hint.py`. Measured below |
| 4 Live Console | done | http://127.0.0.1:8090: chat with streaming, reply groups and batch notes, autopilot; two timelines with totals; pod grids; predictor panel with right/wrong on the next call; think window against the baseline's own cold start; all the requested buttons; footer; light and dark themes; offline (no external URL, tested) |
| 5 Grafana | done | "Skypiea Live: baseline vs predictive" on :3001, 5 s refresh, both namespaces, no plugins (`docs/img/grafana_live.png`) |
| 6 make targets, docs | done | `make live` / `live-clean` / `live-pf` / `live-console` / `live-screenshot`; `docs/LIVE_CONSOLE.md`; `docs/DEMO.md` (7-minute script, troubleshooting, resources); README section |
| 7 fresh-clone verification | done | `~/skypiea-live-fresh2`: `make setup`, 389 tests passed (392 on the final tree here), ruff, `make check-sizes`, `make live` (rc 0, 43.5 s), 4 live conversations (support and coding, hint off and on), the predictor-kill check (PASS), replay, policy switch, export, `make live-clean` (rc 0, ports free) |

Offline tests added: `tests/test_live_tasks.py`, `tests/test_gateway_hint.py`,
`tests/test_service_hint.py`, `tests/test_live_chart.py`, `tests/test_console.py` (17).
Existing defaults are unchanged. The chart renders byte-identical with default values (apart
from the random Grafana password) and is tested. The new tool arguments are off by default
(tested). The CLI agent (`agent/loop.py`) and its tasks behave as before.

## Measured numbers (demo runs, not results)

Source: `results/live_console_demo/` (generated `runs.md`, per-run `summary.json` and
`export.csv`). One live conversation per row, llama3.1:8b at temperature 0, local kind on a
laptop (cold starts lower than AWS), both stacks on one node, baseline scale-to-zero, mirror
concurrent. The LLM chose the same tool sequence with the hint off and on in both demos.

### Turn-start hint: off vs on (the first call of the conversation)

| demo | first tool | LLM think ms, hint off / on | baseline wall ms, off / on | Skypiea wall ms, off / on | first call warmed by hint (on) | calls warmed by a hint, off / on |
|---|---|---|---|---|---|---|
| support | user_lookup | 579 / 722 | 1,986 / 2,014 | 2,920 / 1,856 | yes | 0 / 1 |
| coding | read_file | 1,171 / 874 | 2,019 / 1,988 | 2,654 / 1,231 | yes | 0 / 3 |
In both demos, with the hint on, the first call on Skypiea was started by the hint while the
LLM thought (0.7-0.9 s) and waited less than with the hint off (support 1,856 vs 2,920 ms;
coding 1,231 vs 2,654 ms). The baseline's first call took about 2 s both times. This is one
conversation per cell, and variance is large: the hint-off Skypiea first calls (2.9 and
2.7 s) were slower than the baseline's (2.0 s) in the same conversation, with node contention
recorded. In the final smoke check (not a measurement, `.live/runs/final_smoke`), the
hint-warmed user_lookup took 1,919 ms against 1,909 ms on the baseline. So the hint starts the
pod earlier, but with a 0.6-0.9 s think time it hides only part of a 2-3 s start, and
sometimes nothing visible.

### All verification runs

| run | hint | calls | agent waited on tools, baseline / Skypiea ms | cold calls baseline / Skypiea | added p95 ms baseline / Skypiea | idle pod-s baseline / Skypiea | calls warmed by the hint |
|---|---|---|---|---|---|---|---|
| support_hint_off | off | 6 | 7,548 / 5,311 | 3 / 2 (of 5 / 5 ok) | 2,966 / 2,887 | 17.9 / 36.7 | 0 |
| support_hint_on | on | 6 | 6,322 / 3,576 | 3 / 2 (of 5 / 5 ok) | 2,577 / 1,825 | 18.6 / 50.5 | 1 |
| coding_hint_off | off | 5 | 8,096 / 6,461 | 3 / 2 (of 5 / 5 ok) | 3,947 / 3,664 | 21.1 / 92.4 | 0 |
| coding_hint_on | on | 5 | 7,913 / 4,240 | 3 / 2 (of 5 / 5 ok) | 2,965 / 2,881 | 20.1 / 99.2 | 3 |
| support_predictor_killed | on | 3 | 5,002 / 4,239 | 2 / 2 (of 2 / 2 ok) | 2,977 / 2,479 | 8.3 / 11.1 | 0 |


Calls whose baseline gap differed from the Skypiea gap by more than 100 ms:

Per-call tables: `results/live_console_demo/runs.md`.

What the runs show, plainly:

- The **first call** is cold on both stacks without the hint, and only partly faster with it.
- **reservation_lookup** after user_lookup was predicted right (p about 0.84) in every support
  run with the predictor up. It was still cold on Skypiea, because the LLM thought for 0.35-0.7 s and the start
  takes about 2-3 s.
- Where Skypiea was clearly faster, a pod was already warm from an **earlier** decision:
  transfer_to_human (escalate -> post_comment, warmed after reservation_lookup) about 31 ms vs
  1.6-2.5 s, and `shell` in coding about 30 ms vs 2.0-2.9 s.
- Skypiea always had **more idle pod-seconds** (support 36.7-50.5 vs 17.9-18.6; coding
  92.4-99.2 vs 20.1-21.1).
- **Predictor killed:** every call that succeeded on the baseline also succeeded on Skypiea.
  flight_search failed on both stacks, because the LLM sent an invalid `day`.

## Bugs found during verification (fixed, disclosed)

1. **Mirror fairness, wait for both.** The first design waited for both stacks before the
   LLM's next step, which gave Skypiea the slow baseline's time as free lead time. Two runs
   were discarded and kept as evidence, marked INVALID, in
   `results/live_console_demo/invalid_wait_for_both_mirror/`. Fixed with the gap-matched
   baseline lane (1fe5fdf).
2. **Lane submit timing.** The baseline call was queued only after the predictive call
   returned, so a slow predictive call delayed the baseline. This affected 4 calls in the
   verification runs (listed above). Each was the first use of its server, cold on the
   scale-to-zero baseline regardless of the gap. The hint comparison is Skypiea vs Skypiea.
   Fixed in c4c8e9c. One gap difference is **unavoidable** in live mode: when a predictive
   call is slower than the baseline's, the next call does not exist until the LLM has the
   predictive result. The console tags it and records both gaps. It can only make the baseline
   colder, never warmer.
3. **Record order:** `export.csv` of `coding_hint_on` is in record order. Fixed in d17b6d9;
   runs.md orders calls by measured start.
4. **Autopilot stop** reported "off" while the last turn was still running, so the first kill
   check missed its turn-2 calls. Fixed in 9c701f9, and the kill check was redone; the first
   attempt is in `.live/runs/support_predictor_killed_autopilot_race` of the fresh clone.
5. Agent robustness, all visible on screen and in the CSV: tool calls the 8B model **writes
   as JSON text** are recovered and flagged `tool_call_from_text`. `null` arguments are
   dropped. On a tool error the LLM sees the tool's own message.
6. **Grafana** pods were evicted in a loop on the old stack (plugin downloads); fixed in v1.1.
   Static UI files are served with `no-cache`.

## Screenshots (`docs/img/`, from a locally installed Chrome, `?snapshot=1` view)

| File | What |
|---|---|
| `console_support_hint_off.png`, `console_support_hint_on.png` | airline support, hint off / on |
| `console_coding_hint_off.png`, `console_coding_hint_on.png` | code fix, hint off / on (calls written as text are visible) |
| `console_support_predictor_killed.png` | predictor down: decisions `error:ConnectError`, calls succeed |
| `console_backup_dark.png`, `console_backup_light.png` | the backup session (docs/live_backup/), both themes |
| `grafana_live.png` | the live Grafana dashboard after the runs |

Backup for the talk: `docs/live_backup/` (screenshots, CSV, summary, session log, `make live`
transcript).

## Not done, or only partly

- **Playwright smoke test: skipped.** Playwright is not installed, and adding it needs a new
  package, which you asked to approve first. The screenshots come from the installed Windows
  Chrome (`demo/screenshot.sh`) instead. No browser test drives the UI; the routes and engine
  are tested offline with a TestClient and fakes.
- **`make live` on a laptop without the datasets** cannot build the real predictor image (it
  needs `make data-all model-all predictor-image`). It fails with that message. `make demo`
  (v1.1) works without the datasets.
- The **hint measurement** is one conversation per cell, as asked. It is not a result.
- The **v1.1 `skypiea-tools` demo stack is gone.** At 23:02 on 2026-11-05 the kind cluster
  and the registry container had been removed by something outside this session, and
  `deploy/kind/up.sh` re-created them. `make demo` re-creates that stack.
- **Coding demo coherence:** the coding tools are stateless mocks, so an edit is not visible
  to `run_tests` (each tool is its own pod). The docs say so.

## Risks for a live presentation

- **Docker Desktop / kind on WSL** had three incidents in two days: a stale API port, port
  publishing failing with 500, and the cluster and registry containers gone (cause unknown). Check before the talk: `kubectl --context kind-skypiea get nodes`
  and the port test in DEMO.md. Keep `docs/live_backup/` open as a fallback.
- **Ollama cold model:** the first answer after idle takes seconds. Send a warm-up message.
- **The 8B model's behaviour:** it may ask the customer to confirm, write calls as text
  (handled and flagged), send bad arguments (it gets the error and usually retries), or call
  transfer_to_human on "that's all". The autopilot script is tested; free typing may wander.
- **The effect is small and variable on a laptop:** one node, up to 4 concurrent starts,
  sub-second think times. Say the honest lines in DEMO.md and do not promise a speed-up.
  Skypiea always shows more idle pod-seconds.
- The demo switch (helm) takes 30-60 s; talk through it, or start with `LIVE_DEMO=coding`.

## Commands for the morning

```bash
cd ~/skypiea-infra && git log --oneline feat/agent..feat/live-console   # review the branch
kubectl --context kind-skypiea get pods -n skypiea-base; kubectl --context kind-skypiea get pods -n skypiea-pred
make live-pf       # restart the loopback port-forwards if the console shows errors
# open http://127.0.0.1:8090 (console) and http://127.0.0.1:3001 (Grafana)
curl -s http://localhost:11434/v1/models | head -c 100      # LLM reachable?
.venv/bin/python -m demo.console_run --name morning_check --demo airline --hint on --turns 1
make live          # if anything is down: re-deploys both stacks and restarts the console (~45 s)
make live-clean    # when done (cluster and registry stay)
cat results/live_console_demo/runs.md
```

Scratch fresh clones to delete when you are done: `~/skypiea-live-fresh`,
`~/skypiea-live-fresh2` (and `~/skypiea-fresh` from v1.1). `infra_context.txt` (untracked) was
left alone.
