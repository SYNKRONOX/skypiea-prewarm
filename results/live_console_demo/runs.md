DEMO RUNS, NOT RESULTS: one live conversation per row (llama3.1:8b, temperature 0), local kind cluster on a laptop (cold starts lower than AWS), both stacks on one node.

| run | hint | calls | agent waited on tools, baseline / Skypiea ms | cold calls baseline / Skypiea | added p95 ms baseline / Skypiea | idle pod-s baseline / Skypiea | calls warmed by the hint |
|---|---|---|---|---|---|---|---|
| support_hint_off | off | 6 | 7,548 / 5,311 | 3 / 2 (of 5 / 5 ok) | 2,966 / 2,887 | 17.9 / 36.7 | 0 |
| support_hint_on | on | 6 | 6,322 / 3,576 | 3 / 2 (of 5 / 5 ok) | 2,577 / 1,825 | 18.6 / 50.5 | 1 |
| coding_hint_off | off | 5 | 8,096 / 6,461 | 3 / 2 (of 5 / 5 ok) | 3,947 / 3,664 | 21.1 / 92.4 | 0 |
| coding_hint_on | on | 5 | 7,913 / 4,240 | 3 / 2 (of 5 / 5 ok) | 2,965 / 2,881 | 20.1 / 99.2 | 3 |
| support_predictor_killed | on | 3 | 5,002 / 4,239 | 2 / 2 (of 2 / 2 ok) | 2,977 / 2,479 | 8.3 / 11.1 | 0 |

Calls whose baseline gap differed from the Skypiea gap by more than 100 ms:

- support_hint_off: reservation_lookup (turn 1) baseline gap 1,016 ms vs Skypiea 701 ms
- coding_hint_off: run_tests (turn 1) baseline gap 6,005 ms vs Skypiea 4,322 ms
- coding_hint_on: run_tests (turn 1) baseline gap 5,007 ms vs Skypiea 4,086 ms
- support_predictor_killed: reservation_lookup (turn 1) baseline gap 1,005 ms vs Skypiea 491 ms

**support_hint_off** (hint off, mirror concurrent, baseline scale_to_zero, support profile, trained on tau2-bench traces, lgbm-support-aabdfd2, LLM llama3.1:8b)

| # | turn.reply.pos | tool | LLM think ms | baseline wall ms (cold) | Skypiea wall ms (cold) | Skypiea predicted (source) | gap ms baseline / Skypiea | node starts (max) | both ok |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.1.0 | user_lookup | 579 | 1,986 (cold) | 2,920 (cold) | no | - / - | 2 | yes |
| 2 | 1.2.0 | reservation_lookup | 696 | 2,997 (cold) | 2,302 (cold) | yes (call) | 1,016 / 701 | 2 | yes |
| 3 | 1.3.0 | flight_search | 683 | 10 (warm) | 8 (warm) | no | 689 / 688 | 0 | NO |
| 4 | 2.4.0 | reservation_lookup | 635 | 31 (warm) | 28 (warm) | no | 3,652 / 3,652 | 0 | yes |
| 5 | 2.5.0 | reservation_modify | 1,466 | 30 (warm) | 30 (warm) | no | 1,472 / 1,471 | 0 | yes |
| 6 | 3.6.0 | transfer_to_human | 628 | 2,504 (cold) | 32 (warm) | no | 3,646 / 3,645 | 1 | yes |

**support_hint_on** (hint on, mirror concurrent, baseline scale_to_zero, support profile, trained on tau2-bench traces, lgbm-support-aabdfd2, LLM llama3.1:8b)

| # | turn.reply.pos | tool | LLM think ms | baseline wall ms (cold) | Skypiea wall ms (cold) | Skypiea predicted (source) | gap ms baseline / Skypiea | node starts (max) | both ok |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.1.0 | user_lookup | 722 | 2,014 (cold) | 1,856 (cold) | yes (hint) | - / - | 2 | yes |
| 2 | 1.2.0 | reservation_lookup | 372 | 2,608 (cold) | 1,632 (cold) | yes (call) | 378 / 377 | 2 | yes |
| 3 | 1.3.0 | flight_search | 565 | 10 (warm) | 6 (warm) | no | 572 / 571 | 0 | NO |
| 4 | 2.4.0 | reservation_lookup | 612 | 31 (warm) | 29 (warm) | no | 3,627 / 3,626 | 0 | yes |
| 5 | 2.5.0 | reservation_modify | 1,455 | 30 (warm) | 28 (warm) | no | 1,463 / 1,461 | 0 | yes |
| 6 | 3.6.0 | transfer_to_human | 617 | 1,639 (cold) | 31 (warm) | no | 3,644 / 3,643 | 1 | yes |

**coding_hint_off** (hint off, mirror concurrent, baseline scale_to_zero, coding profile: model trained on TraceLab traces, warm policy tuned on TraceLab + Copilot, lgbm-coding-af00023, LLM llama3.1:8b)

| # | turn.reply.pos | tool | LLM think ms | baseline wall ms (cold) | Skypiea wall ms (cold) | Skypiea predicted (source) | gap ms baseline / Skypiea | node starts (max) | both ok |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.1.0 | read_file | 1,171 | 2,019 (cold) | 2,654 (cold) | no | - / - | 2 | yes |
| 2 | 1.2.0 | run_tests (written as text) | 4,316 | 3,998 (cold) | 3,703 (cold) | no | 6,005 / 4,322 | 1 | yes |
| 3 | 1.3.0 | run_tests (written as text) | 3,879 | 38 (warm) | 37 (warm) | no | 3,885 / 3,884 | 0 | yes |
| 4 | 2.4.0 | run_tests | 551 | 38 (warm) | 36 (warm) | yes (call) | 4,564 / 4,563 | 0 | yes |
| 5 | 3.5.0 | shell | 496 | 2,004 (cold) | 31 (warm) | yes (call) | 4,511 / 4,510 | 1 | yes |

**coding_hint_on** (hint on, mirror concurrent, baseline scale_to_zero, coding profile: model trained on TraceLab traces, warm policy tuned on TraceLab + Copilot, lgbm-coding-af00023, LLM llama3.1:8b)

| # | turn.reply.pos | tool | LLM think ms | baseline wall ms (cold) | Skypiea wall ms (cold) | Skypiea predicted (source) | gap ms baseline / Skypiea | node starts (max) | both ok |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.1.0 | read_file | 874 | 1,988 (cold) | 1,231 (cold) | yes (hint) | - / - | 2 | yes |
| 2 | 1.2.0 | run_tests (written as text) | 4,080 | 2,998 (cold) | 2,913 (cold) | no | 5,007 / 4,086 | 1 | yes |
| 3 | 1.3.0 | run_tests (written as text) | 3,770 | 30 (warm) | 36 (warm) | no | 3,774 / 3,773 | 0 | yes |
| 4 | 2.4.0 | run_tests | 537 | 37 (warm) | 30 (warm) | yes (hint) | 3,812 / 3,812 | 0 | yes |
| 5 | 3.5.0 | shell | 507 | 2,860 (cold) | 30 (warm) | yes (hint) | 3,524 / 3,523 | 1 | yes |

**support_predictor_killed** (hint on, mirror concurrent, baseline scale_to_zero, support profile, trained on tau2-bench traces, None, LLM llama3.1:8b)

| # | turn.reply.pos | tool | LLM think ms | baseline wall ms (cold) | Skypiea wall ms (cold) | Skypiea predicted (source) | gap ms baseline / Skypiea | node starts (max) | both ok |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1.1.0 | user_lookup | 617 | 1,995 (cold) | 1,730 (cold) | no | - / - | 2 | yes |
| 2 | 1.2.0 | reservation_lookup | 485 | 3,007 (cold) | 2,509 (cold) | no | 1,005 / 491 | 1 | yes |
| 3 | 1.3.0 | flight_search | 802 | 9 (warm) | 7 (warm) | no | 808 / 807 | 0 | NO |

