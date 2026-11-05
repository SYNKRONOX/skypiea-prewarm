# Coding retune under the scale-up cap, cap sensitivity, final tables

Cold starts: kind (sha256 96d8a3d6952d for the first run) and AWS (sha256 00758310b4d6). git 365a4fc-dirty. E0 measured 1 and 3 starts at once, NOT 2: in every capped run a start that overlaps another takes the 3-at-once (parallel_3) time, also when only 2 overlap. Each session is its own node (per-session pods, no shared pool).

## Retune (VAL only, cached, AWS, cap 2, parallel_3)

Rule: max mean VAL equal-idle margin vs keep-alive among settings with idle <= 0.3 x always-warm on every source. Chosen: **copies1 lam100** (overrides {"max_copies_per_tool": 1}), VAL mean margin +3.56 pts, mean warm 62.4%. The frozen profile on the same VAL: margin +0.24, warm 65.4%. The frozen rule (max warm within the idle budget) would pick frozen lam100 (margin +0.24, warm 65.4%).

Best setting per variant (VAL margin, mean of TraceLab and Copilot):

| Variant | best threshold | mean margin | mean warm % | TraceLab warm / idle | Copilot warm / idle |
|---|---|---|---|---|---|
| copies1 | lam100 | +3.56 | 62.4 | 61.2 / 28,863 | 63.7 / 34,899 |
| copies1,stagger3s | lam100 | +3.54 | 62.8 | 61.4 / 29,678 | 64.1 / 35,736 |
| top2,copies1 | lam100 | +3.10 | 58.9 | 56.8 / 23,372 | 61.0 / 29,803 |
| copies1,stagger6s | lam100 | +2.89 | 62.4 | 61.0 / 30,224 | 63.7 / 36,379 |
| top2,copies1,stagger6s | lam30 | +2.80 | 56.2 | 54.0 / 20,516 | 58.4 / 25,773 |
| top1,copies1 | tau0.3 | +2.27 | 54.1 | 52.3 / 19,155 | 55.9 / 22,948 |
| frozen | lam100 | +0.24 | 65.4 | 66.4 / 56,778 | 64.4 / 45,834 |
| top1 | tau0.9 | -0.19 | 49.7 | 49.5 / 17,788 | 49.8 / 19,475 |
| top2 | tau0.9 | -0.19 | 49.7 | 49.5 / 17,788 | 49.8 / 19,475 |

## Final: frozen vs retuned vs keep-alive 30 s (TEST)

Cells: warm % / idle pod-s per 1,000 calls (equal-idle margin vs keep-alive, points). AWS = no cap, no contention; AWS + cap = cap 2, parallel_3.

| Source | Mode | Policy | kind | AWS | AWS + cap |
|---|---|---|---|---|---|
| tracelab | cached | keep_alive_30 | 58.5 / 18,090 | 58.4 / 18,084 | 56.9 / 17,622 |
| tracelab | cached | coding_frozen | 75.3 / 54,932 (+1.2) | 76.0 / 57,219 (+1.5) | 70.8 / 53,587 (-1.9) |
| tracelab | cached | coding_retuned | 70.0 / 27,589 (+4.6) | 70.5 / 28,398 (+4.7) | 67.6 / 27,387 (+3.5) |
| tracelab | uncached | keep_alive_30 | 57.8 / 18,207 | 56.6 / 18,472 | 53.4 / 17,768 |
| tracelab | uncached | coding_frozen | 77.6 / 64,247 (+2.2) | 77.8 / 68,772 (+2.4) | 69.4 / 61,936 (-2.4) |
| tracelab | uncached | coding_retuned | 71.8 / 32,099 (+5.1) | 71.7 / 34,243 (+5.3) | 66.5 / 32,361 (+3.4) |
| copilot_2026 | cached | keep_alive_30 | 51.4 / 19,476 | 51.1 / 19,511 | 49.6 / 19,301 |
| copilot_2026 | cached | coding_frozen | 69.4 / 46,411 (+5.2) | 69.6 / 47,311 (+5.4) | 64.8 / 45,556 (+2.4) |
| copilot_2026 | cached | coding_retuned | 67.4 / 34,642 (+6.6) | 67.6 / 35,303 (+6.8) | 64.2 / 34,541 (+5.1) |
| copilot_2026 | uncached | keep_alive_30 | 49.2 / 19,991 | 47.4 / 20,479 | 44.7 / 20,109 |
| copilot_2026 | uncached | coding_frozen | 68.8 / 50,723 (+5.7) | 67.4 / 53,100 (+5.7) | 60.6 / 49,984 (+1.9) |
| copilot_2026 | uncached | coding_retuned | 66.9 / 38,448 (+7.1) | 65.5 / 40,755 (+7.1) | 60.7 / 39,202 (+5.0) |
| tau2_bench | cached | keep_alive_30 | 56.1 / 18,299 | 56.4 / 18,232 | 56.4 / 18,218 |
| tau2_bench | cached | support_frozen | 64.0 / 12,106 (+18.5) | 64.0 / 12,120 (+18.1) | 61.4 / 11,859 (+15.7) |
| tau2_bench | uncached | keep_alive_30 | 55.6 / 18,319 | 52.6 / 18,973 | 50.3 / 19,413 |
| tau2_bench | uncached | support_frozen | 62.3 / 11,920 (+16.1) | 60.2 / 12,041 (+17.7) | 58.8 / 11,990 (+18.2) |

## Sensitivity to the cap (TEST; equal-idle margin vs keep-alive, points)

All AWS cold starts with parallel_3 contention; "uncapped" = no cap, same contention. Report only: nothing was tuned on these.

| Source | Mode | Policy | cap 1 | cap 2 | cap 3 | uncapped |
|---|---|---|---|---|---|---|
| tracelab | cached | coding_frozen | -1.3 | -1.9 | -0.9 | +0.7 |
| tracelab | cached | coding_retuned | +3.6 | +3.5 | +4.0 | +4.3 |
| tracelab | uncached | coding_frozen | -3.0 | -2.4 | -0.9 | +1.5 |
| tracelab | uncached | coding_retuned | +2.8 | +3.4 | +4.2 | +4.9 |
| copilot_2026 | cached | coding_frozen | +2.6 | +2.4 | +3.4 | +3.9 |
| copilot_2026 | cached | coding_retuned | +5.2 | +5.1 | +5.5 | +5.6 |
| copilot_2026 | uncached | coding_frozen | +0.6 | +1.9 | +3.5 | +4.5 |
| copilot_2026 | uncached | coding_retuned | +3.8 | +5.0 | +5.8 | +6.1 |
| tau2_bench | cached | support_frozen | +16.0 | +15.7 | +15.7 | +15.7 |
| tau2_bench | uncached | support_frozen | +15.6 | +18.2 | +18.4 | +18.4 |

## Oracle (TEST, warm % / idle pod-s per 1k)

`oracle` knows the next batch and starts each pod one cold start before it. With no cap and no contention (AWS) it is the headroom ceiling. With contention or a cap it is **not achievable**: its just-in-time starts are slowed or queued. `oracle_cap` also knows the cap and asks for all of a batch's starts ceil(n / cap) waves early (a simple plan, not an optimum; at cap 1 no start overlaps another, so none takes the slower parallel_3 time).

| Source | Mode | oracle, AWS no cap, no contention (ceiling) | oracle, uncapped + parallel_3 (not achievable) | oracle, cap 2 (not achievable) | oracle_cap, cap 1 | oracle_cap, cap 2 | oracle_cap, cap 3 |
|---|---|---|---|---|---|---|---|
| tracelab | cached | 91.1 / 568 | 55.6 / 570 | 54.0 / 551 | 76.6 / 1,206 | 72.5 / 1,269 | 79.2 / 1,087 |
| tracelab | uncached | 67.8 / 569 | 36.1 / 570 | 34.0 / 529 | 42.8 / 1,136 | 42.3 / 1,128 | 47.1 / 986 |
| copilot_2026 | cached | 79.7 / 4,704 | 60.3 / 4,710 | 59.7 / 4,661 | 69.6 / 4,829 | 66.1 / 4,866 | 67.6 / 4,851 |
| copilot_2026 | uncached | 57.8 / 4,715 | 41.2 / 4,722 | 40.3 / 4,654 | 45.6 / 4,857 | 44.8 / 4,881 | 45.8 / 4,860 |
| tau2_bench | cached | 78.1 / 524 | 72.5 / 533 | 72.5 / 532 | 74.0 / 526 | 72.5 / 532 | 72.5 / 533 |
| tau2_bench | uncached | 53.2 / 503 | 38.9 / 502 | 38.9 / 499 | 40.4 / 504 | 38.9 / 499 | 38.9 / 501 |
