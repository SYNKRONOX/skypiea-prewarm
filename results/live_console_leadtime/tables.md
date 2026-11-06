DEMO RUNS, NOT RESULTS. Autopilot with the fixed customer script; the scripted customer types at an assumed 200 characters per minute in every cell (a typing model, not a measurement); the LLM (llama3.1:8b, temperature 0) is live and picks its own tools. Local kind on a laptop, both stacks on one node, images pre-pulled (cached starts), console tool mapping on, baseline scale-to-zero, mirror concurrent. Medians over the n conversations of a cell.

## airline

| lookahead | hint | n | agent waited on tools, median per conversation: baseline / Skypiea ms | cold calls baseline / Skypiea (of calls) | idle pod-s, median per conversation: baseline / Skypiea | abandoned turns |
|---|---|---|---|---|---|---|
| 1 | hint off | 3 | 15,742 / 4,401 | 16 / 6 (of 22) | 29.7 / 121.9 | 0 |
| 1 | hint at Enter | 3 | 15,357 / 4,495 | 16 / 6 (of 22) | 29.5 / 147.5 | 0 |
| 1 | hint at typing start | 3 | 17,061 / 4,352 | 18 / 6 (of 24) | 38.6 / 201.7 | 0 |
| 2 | hint off | 3 | 18,038 / 8,550 | 17 / 12 (of 23) | 38.8 / 132.9 | 0 |
| 2 | hint at Enter | 3 | 16,943 / 5,518 | 17 / 8 (of 23) | 38.0 / 179.7 | 0 |
| 2 | hint at typing start | 3 | 14,265 / 8,485 | 15 / 11 (of 21) | 29.7 / 209.9 | 0 |

First call of each turn (median wall ms over the n conversations; 'warm k/n' = the call found a ready pod; lead = first hint of the turn to Enter):

| lookahead | hint | turn | first tool(s) | baseline ms (warm k/n) | Skypiea ms (warm k/n) | lead ms |
|---|---|---|---|---|---|---|
| 1 | hint off | 1 | user_lookup | 1,615 (0/3) | 1,629 (0/3) | - |
| 1 | hint off | 2 | reservation_lookup | 3,183 (0/3) | 33 (3/3) | - |
| 1 | hint off | 3 | transfer_to_human | 3,434 (0/3) | 34 (3/3) | - |
| 1 | hint at Enter | 1 | user_lookup | 1,891 (0/3) | 1,881 (0/3) | 0 |
| 1 | hint at Enter | 2 | reservation_lookup | 3,028 (0/3) | 33 (3/3) | 0 |
| 1 | hint at Enter | 3 | transfer_to_human | 3,972 (0/3) | 33 (3/3) | 0 |
| 1 | hint at typing start | 1 | user_lookup | 1,678 (0/3) | 1,681 (0/3) | 43,072 |
| 1 | hint at typing start | 2 | reservation_lookup | 3,078 (0/3) | 33 (3/3) | 12,305 |
| 1 | hint at typing start | 3 | transfer_to_human | 3,422 (0/3) | 34 (3/3) | 5,705 |
| 2 | hint off | 1 | user_lookup | 2,548 (0/3) | 1,849 (0/3) | - |
| 2 | hint off | 2 | reservation_lookup | 2,994 (0/3) | 36 (3/3) | - |
| 2 | hint off | 3 | transfer_to_human | 3,602 (0/3) | 3,820 (0/3) | - |
| 2 | hint at Enter | 1 | user_lookup | 1,665 (0/3) | 1,564 (0/3) | 0 |
| 2 | hint at Enter | 2 | reservation_lookup | 3,004 (0/3) | 33 (3/3) | 0 |
| 2 | hint at Enter | 3 | transfer_to_human | 3,472 (0/3) | 3,785 (0/3) | 0 |
| 2 | hint at typing start | 1 | user_lookup | 1,958 (0/3) | 1,968 (0/3) | 43,073 |
| 2 | hint at typing start | 2 | reservation_lookup | 2,962 (0/3) | 33 (3/3) | 12,305 |
| 2 | hint at typing start | 3 | transfer_to_human | 3,026 (0/3) | 3,871 (1/3) | 5,705 |

LLM behaviour per cell (lookahead/hint): 1/off: 3 calls written as text, 3 calls failed on a stack; 1/enter: 3 calls written as text, 3 calls failed on a stack; 1/typing: 3 calls written as text, 3 calls failed on a stack; 2/off: 3 calls written as text, 3 calls failed on a stack; 2/enter: 3 calls written as text, 3 calls failed on a stack; 2/typing: 3 calls written as text, 3 calls failed on a stack

## codefix

| lookahead | hint | n | agent waited on tools, median per conversation: baseline / Skypiea ms | cold calls baseline / Skypiea (of calls) | idle pod-s, median per conversation: baseline / Skypiea | abandoned turns |
|---|---|---|---|---|---|---|
| 1 | hint off | 3 | 13,331 / 9,579 | 16 / 12 (of 24) | 35.2 / 235.0 | 0 |
| 1 | hint at Enter | 3 | 13,520 / 10,286 | 15 / 12 (of 24) | 32.2 / 250.9 | 0 |
| 1 | hint at typing start | 3 | 14,318 / 9,475 | 15 / 12 (of 24) | 35.5 / 332.0 | 0 |
| 2 | hint off | 3 | 17,350 / 7,394 | 17 / 9 (of 24) | 35.5 / 270.8 | 0 |
| 2 | hint at Enter | 3 | 13,681 / 8,084 | 16 / 9 (of 24) | 36.5 / 274.9 | 0 |
| 2 | hint at typing start | 3 | 13,330 / 7,347 | 15 / 9 (of 24) | 32.7 / 355.5 | 0 |

First call of each turn (median wall ms over the n conversations; 'warm k/n' = the call found a ready pod; lead = first hint of the turn to Enter):

| lookahead | hint | turn | first tool(s) | baseline ms (warm k/n) | Skypiea ms (warm k/n) | lead ms |
|---|---|---|---|---|---|---|
| 1 | hint off | 1 | read_file | 1,833 (0/3) | 1,994 (0/3) | - |
| 1 | hint off | 2 | run_tests | 3,488 (0/3) | 42 (3/3) | - |
| 1 | hint off | 3 | shell | 2,110 (0/3) | 2,132 (0/3) | - |
| 1 | hint at Enter | 1 | read_file | 1,895 (0/3) | 2,896 (0/3) | 0 |
| 1 | hint at Enter | 2 | run_tests | 4,146 (0/3) | 42 (3/3) | 0 |
| 1 | hint at Enter | 3 | shell | 2,055 (0/3) | 2,153 (0/3) | 0 |
| 1 | hint at typing start | 1 | read_file | 2,067 (0/3) | 2,096 (0/3) | 38,867 |
| 1 | hint at typing start | 2 | run_tests | 4,155 (0/3) | 40 (3/3) | 14,405 |
| 1 | hint at typing start | 3 | shell | 2,886 (0/3) | 2,104 (0/3) | 13,207 |
| 2 | hint off | 1 | read_file | 2,010 (0/3) | 1,976 (0/3) | - |
| 2 | hint off | 2 | run_tests | 3,137 (0/3) | 42 (3/3) | - |
| 2 | hint off | 3 | shell | 2,251 (0/3) | 2,151 (0/3) | - |
| 2 | hint at Enter | 1 | read_file | 1,954 (0/3) | 2,781 (0/3) | 0 |
| 2 | hint at Enter | 2 | run_tests | 4,095 (0/3) | 40 (3/3) | 0 |
| 2 | hint at Enter | 3 | shell | 1,920 (0/3) | 2,210 (0/3) | 0 |
| 2 | hint at typing start | 1 | read_file | 1,992 (0/3) | 1,976 (0/3) | 38,866 |
| 2 | hint at typing start | 2 | run_tests | 4,088 (0/3) | 43 (3/3) | 14,405 |
| 2 | hint at typing start | 3 | shell | 1,909 (0/3) | 2,124 (0/3) | 13,208 |

LLM behaviour per cell (lookahead/hint): 1/off: 9 calls written as text, 0 calls failed on a stack; 1/enter: 9 calls written as text, 0 calls failed on a stack; 1/typing: 9 calls written as text, 0 calls failed on a stack; 2/off: 9 calls written as text, 0 calls failed on a stack; 2/enter: 9 calls written as text, 0 calls failed on a stack; 2/typing: 9 calls written as text, 0 calls failed on a stack

## Where Skypiea is slower or costs more

- airline, lookahead 1, hint off: Skypiea COST MORE idle pod-seconds (median 121.9 vs 29.7).
- airline, lookahead 1, hint at Enter: Skypiea COST MORE idle pod-seconds (median 147.5 vs 29.5).
- airline, lookahead 1, hint at typing start: Skypiea COST MORE idle pod-seconds (median 201.7 vs 38.6).
- airline, lookahead 2, hint off: Skypiea COST MORE idle pod-seconds (median 132.9 vs 38.8).
- airline, lookahead 2, hint at Enter: Skypiea COST MORE idle pod-seconds (median 179.7 vs 38.0).
- airline, lookahead 2, hint at typing start: Skypiea COST MORE idle pod-seconds (median 209.9 vs 29.7).
- codefix, lookahead 1, hint off: Skypiea COST MORE idle pod-seconds (median 235.0 vs 35.2).
- codefix, lookahead 1, hint at Enter: Skypiea COST MORE idle pod-seconds (median 250.9 vs 32.2).
- codefix, lookahead 1, hint at typing start: Skypiea COST MORE idle pod-seconds (median 332.0 vs 35.5).
- codefix, lookahead 2, hint off: Skypiea COST MORE idle pod-seconds (median 270.8 vs 35.5).
- codefix, lookahead 2, hint at Enter: Skypiea COST MORE idle pod-seconds (median 274.9 vs 36.5).
- codefix, lookahead 2, hint at typing start: Skypiea COST MORE idle pod-seconds (median 355.5 vs 32.7).
