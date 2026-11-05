Source: `results/agent_runs/summary.csv` and `results/agent_runs/summary_think.csv` (python -m agent.summarize).

**Labels:** kind cold starts (laptop: a lower bound), local 8B model, SYNTHETIC predictor profile `prd`, 10 sessions per cell. Real (unscaled) LLM think times. Not comparable with the AWS tables.

| Scenario | Policy | sessions | ok calls | cold % | added p50 ms | added p99 ms | idle pod-s | LLM followed graph | canonical order | session ends |
|---|---|---|---|---|---|---|---|---|---|---|
| A | scale_to_zero | 10 | 33 | 81.8 | 2,522 | 4,158 | 173 | 4/10 | 3/10 | finish=4;no_tool_call=6 |
| A | keep_alive | 10 | 33 | 9.1 | 0 | 3,250 | 243 | 4/10 | 3/10 | finish=3;no_tool_call=7 |
| A | predictive | 10 | 32 | 6.2 | 0 | 2,663 | 202 | 4/10 | 3/10 | finish=4;no_tool_call=6 |
| B | scale_to_zero | 10 | 37 | 32.4 | 0 | 3,947 | 223 | 9/10 | 9/10 | finish=10 |
| B | keep_alive | 10 | 34 | 11.8 | 0 | 3,957 | 270 | 8/10 | 8/10 | finish=10 |
| B | predictive | 10 | 34 | 14.7 | 0 | 2,957 | 307 | 8/10 | 8/10 | finish=10 |

LLM think gap before each tool call (all policies pooled) vs that tool's median cold start in `configs/cold_start.yaml` (AWS; kind starts are shorter). A call that is not the first of a multi-call LLM reply has think gap 0. 'think >= cached' is the share of calls whose think gap alone could hide a cached cold start started at the previous call:

| Tool | calls | think median ms | think p90 ms | cold start cached ms | cold start uncached ms | think >= cached | think >= uncached |
|---|---|---|---|---|---|---|---|
| ALL | 203 | 1,354 | 2,938 | - | - | 8% | 0% |
| format_diff | 25 | 0 | 0 | 2,674 | 5,112 | 0% | 0% |
| order_lookup | 33 | 457 | 743 | 2,743 | 6,310 | 0% | 0% |
| policy_check | 29 | 1,418 | 2,035 | 3,651 | 8,258 | 0% | 0% |
| post_comment | 30 | 0 | 1,024 | 2,574 | 4,474 | 0% | 0% |
| read_file | 25 | 2,938 | 3,202 | 2,730 | 4,301 | 56% | 4% |
| refund_issue | 36 | 1,481 | 2,504 | 2,572 | 4,610 | 6% | 0% |
| run_tests | 25 | 0 | 0 | 3,791 | 8,366 | 0% | 0% |
