# Superseded: code-fix story runs before the runner's "cannot judge" rule (2026-11-06 ~20:30 UTC)

Kept unedited for the record. In these runs neither model fixed the planted bug according to the
simulated runner. llama3.1:8b: every edit_file failed (old text not found / not unique) and it
then said it had changed the file (the Repository card showed "Files changed: none").
qwen2.5:7b: 7 edits of sortkit/sorting.py, tests stayed "4 passed, 2 failed". The runner at that
time judged merge_sort only by the planted `_merge` pattern, and the edits themselves were not
recorded, so it cannot be ruled out that a rewrite was judged "failed" although it might have
been correct. The runner now reports such rewrites as "not evaluated" and the Repository card
shows the diff; the runs were repeated (../story_codefix_act1__*).
