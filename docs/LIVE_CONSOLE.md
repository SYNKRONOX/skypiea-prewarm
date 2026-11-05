# Skypiea Live Console

A presenter types into a chat; a local LLM (Ollama) answers and calls tools; every tool call
runs on **two stacks** in the same kind cluster, "Normal (baseline)" and "Skypiea
(predictive)", and the browser shows how they differ. This file is the design and reference;
the presenter script is in [`DEMO.md`](DEMO.md#live-console-7-minute-presenter-script).

## 1. Vocabulary: real profiles only (no SYNTHETIC profile)

The console uses only tool classes that a **real-data** predictor profile knows. The
vocabularies below are read from the predictor image's bundle manifests by
`python -m agent.vocab --image localhost:5001/skypiea/predictor:af000231bc7b`, which writes
[`configs/live/predictor_vocab.json`](../configs/live/predictor_vocab.json) and
[`configs/live/predictor_probe.json`](../configs/live/predictor_probe.json) (generated, not
typed). `tests/test_live_tasks.py` fails if a console tool leaves the vocabulary.

**Labels used on screen and in logs** (from the manifests, not from the profile names):

- support: *support profile, trained on tau2-bench traces* (bundle `lgbm-support-aabdfd2`,
  manifest `source: tau2_bench`).
- coding: *coding profile: model trained on TraceLab traces, warm policy tuned on TraceLab +
  Copilot*. The request asked for "trained on TraceLab + Copilot traces"; the manifest of
  `lgbm-coding-af00023` says `source: tracelab` (vocabulary: TraceLab's native classes), and
  `configs/model/profiles.yaml` tunes the coding **policy** on both sources' VAL splits. So the
  label says exactly that.

The `prd` profile stays in the repository and in the image; the console never selects it.

Output of `python -m agent.vocab`:

```
predictor image predictor:af000231bc7b
  coding: lgbm-coding-af00023 (source tracelab, synthetic False): agent, build, git, other, package, planning, read_file, run_tests, schedule, search, shell_io, shell_other, user_io, web, write_file
  support: lgbm-support-aabdfd2 (source tau2_bench, synthetic None): escalate, flight_search, other, reservation_book, reservation_cancel, reservation_lookup, reservation_modify, think, user_lookup
| task | profile | LLM tool | gateway class | in vocabulary | server |
|---|---|---|---|---|---|
| airline | support | user_lookup | user_lookup | yes | user_lookup |
| airline | support | reservation_lookup | reservation_lookup | yes | travel_booking |
| airline | support | flight_search | flight_search | yes | travel_booking |
| airline | support | reservation_modify | reservation_modify | yes | travel_booking |
| airline | support | reservation_cancel | reservation_cancel | yes | travel_booking |
| airline | support | transfer_to_human | escalate | yes | post_comment |
| codefix | coding | search | search | yes | search |
| codefix | coding | read_file | read_file | yes | read_file |
| codefix | coding | edit_file | write_file | yes | edit_file |
| codefix | coding | run_tests | run_tests | yes | run_tests |
| codefix | coding | shell | shell_other | yes | shell |

Predictor along the scripted flows (history with 3 s gaps and 200 ms calls; the live gaps differ):
| profile | after | next in flow | mode | top-3 | warms |
|---|---|---|---|---|---|
| support | (turn start, empty) | user_lookup | ranker | user_lookup 0.50, reservation_lookup 0.41, flight_search 0.02 | user_lookup |
| support | user_lookup | reservation_lookup | ranker | reservation_lookup 0.84, flight_search 0.04, reservation_book 0.03 | reservation_lookup |
| support | user_lookup > reservation_lookup | flight_search | ranker | escalate 0.45, flight_search 0.22, reservation_lookup 0.19 | escalate |
| support | user_lookup > reservation_lookup > flight_search | reservation_modify | ranker | flight_search 0.65, reservation_modify 0.10, reservation_cancel 0.06 | flight_search |
| support | user_lookup > reservation_lookup > flight_search > reservation_modify | - | prior | END 0.76, reservation_modify 0.14, reservation_cancel 0.02 | nothing (abstains) |
| coding | (turn start, empty) | search | ranker | search 0.38, read_file 0.29, shell_other 0.15 | git, read_file, search, shell_other |
| coding | search | read_file | ranker | search 0.54, read_file 0.35, shell_other 0.05 | read_file, search |
| coding | search > read_file | write_file | ranker | read_file 0.47, search 0.35, shell_other 0.06 | read_file, search |
| coding | search > read_file > write_file | run_tests | ranker | write_file 0.44, read_file 0.22, search 0.14 | read_file, search, write_file |
| coding | search > read_file > write_file > run_tests | shell_other | prior | shell_io 0.22, run_tests 0.20, write_file 0.20 | nothing (abstains) |
| coding | search > read_file > write_file > run_tests > shell_other | - | ranker | shell_other 0.30, read_file 0.17, shell_io 0.13 | read_file, run_tests, search, shell_io, shell_other |
```

What this means for the demo, said honestly on screen:

- **Support: prediction works on servers, not classes.** `tools/tool_map.yaml` routes all five
  airline classes to one server, `travel_booking`. Once `travel_booking` is warm, the next
  airline call finds it warm whatever the predictor said. The places where prediction can
  matter are: the turn start (user_lookup, p 0.50), user_lookup -> reservation_lookup (p 0.84:
  warms `travel_booking`), and a hand-over (`escalate` -> `post_comment`). After
  reservation_lookup the profile's top guess is `escalate` (0.45), which warms `post_comment`;
  that is what tau2-bench agents often did.
- **Coding:** the first-tool distribution is broad (search 0.38, read_file 0.29), so the
  turn-start hint warms several servers. The profile **abstains** after run_tests (mode
  `prior`: no decision), so `shell` after `run_tests` is never predicted.
- Where the flow diverges from the training traces (the LLM picks its own order), the console
  shows the predictor's top-3 and whether the next call matched.

Mock data: `user_lookup(with_reservations=true)` lists the user's reservation ids and
`travel_booking(detail=true)` returns owner, route, day and the new flight on modify. Both are
new optional arguments, off by default (existing calls answer exactly as before, tested). The
autopilot customer is `priya_menon_1119`, reservation `R35033531` (business, SK216 GOI->HYD,
day 45). The coding tools are deterministic mocks over the sample repo `minicalc`; an edit is
not visible to later calls, since each tool runs in its own pod.
