# Cold calls left under keep-alive 30 s, by cause (test, cached images)

| Source | Profile | cause | % of all calls (cold under KA30) | share ours warms | idle /1k: KA30 -> ours |
|---|---|---|---|---|---|
| tracelab | coding | first_use | 4.8 | 21.7 | 18,090 -> 54,932 |
| tracelab | coding | gap_over_30s | 31.5 | 39.4 | 18,090 -> 54,932 |
| tracelab | coding | other | 5.1 | 64.8 | 18,090 -> 54,932 |
| copilot_2026 | coding | first_use | 9.2 | 30.5 | 19,476 -> 46,410 |
| copilot_2026 | coding | gap_over_30s | 33.1 | 42.3 | 19,476 -> 46,410 |
| copilot_2026 | coding | other | 6.3 | 22.4 | 19,476 -> 46,410 |
| tau2_bench | support | first_use | 38.3 | 24.4 | 18,299 -> 12,106 |
| tau2_bench | support | gap_over_30s | 3.8 | 0.0 | 18,299 -> 12,106 |
| tau2_bench | support | other | 1.8 | 50.0 | 18,299 -> 12,106 |
