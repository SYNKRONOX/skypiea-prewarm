# Backup set W1 (2026-11-06, ~22:40-22:45 UTC): for a talk without cluster or network

Demo runs, not results. One conversation each, recorded with the final W1 code.

| File | What |
|---|---|
| `screen_light.png`, `screen_dark.png` | Story Act 1 (a new customer books a trip), llama3.1:8b: boarding pass with "new customer" badge, "Checked bags: 0 → 1", write log (incl. the refused duplicate booking), truth badges |
| `screen_details.png` | the same, Details drawer open |
| `screen_codefix.png` | Code-fix Act, qwen2.5:7b: Repository card (tests 4 passed, 2 failed → 3 passed, 0 failed, 3 not evaluated), the diff, "simulated test runner" |
| `session_log.txt` | the Act 1 conversation, every tool call on both stacks (warm/cold and why), writes, truth badges, totals |
| `story_*_state.json`, `*_calls.csv`, `*_turns.csv`, `story_*.json` | the console's own state, CSV exports and per-conversation report for both runs |

What to say from it: Normal waited 14.93 s on tools, Skypiea 5.93 s; Skypiea kept pods idle for
212.0 pod-s vs 31.1 (the cost side). The first lookup was warm on Skypiea because of the
typing hint; book_flight was warm because a decision had warmed its server; the duplicate
booking was refused by the tool; Skye's "$114" (her own sum of $79 + $35) is flagged "Not from
a tool". In the code-fix run the model's rewrite of merge_sort could not be judged by the
simulated runner, so it says "not evaluated" instead of claiming a pass.
