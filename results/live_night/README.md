# W1 night live smoke: one conversation per suite scenario (DEMO RUNS, NOT RESULTS)

2026-11-07, branch feat/demo-realism (code at b2f82d3 + the suite Auto-play commit), the real
kind cluster (`kind-skypiea`) with both stacks (Normal = scale-to-zero, Skypiea = support
predictor), llama3.1:8b, hint off, one conversation each, scripted customer lines from the
scenario suite (variant named in the file). Driven through the console's own API
(`console.bakeoff.run` with `scenario: suite:<case>`). One laptop, one conversation per row:
these are demo runs, not results.

| Scenario (variant) | End state (oracle) | Calls ok Normal / Skypiea | Cold Normal / Skypiea | Waited on tools Normal / Skypiea | Idle pod-s Normal / Skypiea |
|---|---|---|---|---|---|
| New customer books a trip (typos) | expected | 5/5 / 5/5 | 5 / 4 | 13.2 s / 11.2 s | 23.6 / 103.0 |
| Existing customer books with a promo (wrong email) | expected | 6/6 / 6/6 | 6 / 6 | 15.0 s / **16.1 s** | 29.4 / 137.9 |
| Change the date (changes mind) | expected | 5/5 / 5/5 | 5 / 2 | 12.2 s / 6.2 s | 22.8 / 99.7 |
| Add checked baggage (unsupported request) | expected | 5/5 / 5/5 | 5 / 2 | 12.7 s / 4.6 s | 22.3 / 92.9 |
| Cancel with refund (typos) | expected | 5/5 / 5/5 | 5 / 3 | 13.7 s / 8.7 s | 22.8 / 78.9 |
| Out of scope → transfer (unsupported) | expected | 3/3 / 3/3 | 2 / 2 | 5.8 s / 5.8 s | 10.5 / 10.3 |

All 29 calls succeeded on both stacks. In the promo conversation Skypiea was cold on every
call and waited longer than Normal (offers are outside the predictor's vocabulary; the calls
before them were not predicted in time). Skypiea's idle pod-seconds were 3.4-4.7 times
Normal's where it warmed pods (the cost side); in the transfer conversation they were equal.

Files per conversation: `*.json` (console report, incl. the oracle verdict), `*_calls.csv`,
`*_turns.csv`, `*_state.json` (console state at the end).
