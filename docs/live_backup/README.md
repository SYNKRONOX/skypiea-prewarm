# Backup of one full Live Console session (for a live presentation)

If the laptop, Docker or Ollama fails during the talk, show these instead. Recorded on
2026-11-06 from the fresh clone `~/skypiea-live-fresh2` at commit f6d8e07 (final console code
apart from the no-cache header). Airline support demo, autopilot customer script, turn-start
hint ON, mirror concurrent, baseline scale-to-zero, llama3.1:8b on local Ollama. Demo run, not
a result.

| File | What |
|---|---|
| `../img/console_backup_dark.png`, `../img/console_backup_light.png` | the console at the end of the session (`?snapshot=1`, dark and light theme) |
| `export.csv` | the console's CSV export (metadata only: no prompts, arguments or results) |
| `summary.json` | the console's totals and per-call rows |
| `session_log.txt` | the same rows as text, in call order |
| `make_live_transcript.log` | terminal log of `make live` from the fresh clone |

The other screenshots in `docs/img/` are from the verification runs
(`results/live_console_demo/`). `grafana_live.png` shows the Grafana dashboard after those
runs.

## Phase B: Skypiea Air view (`phase_b/`)

Recorded 2026-11-06, 15:41-15:44 UTC, by `demo/verify_phase_b.sh` (run `change_kill`):
Skypiea Air demo, autopilot scenario "change" (Tara Patel), hint on typing, **predictor killed
before the conversation**, llama3.1:8b on local Ollama, mirror concurrent, Normal =
scale-to-zero. Demo run, not a result. This is the run where the truth badge fired by itself:
the change_flight call was refused (the model sent no booking code), and Skye's last reply
still said "I changed your flight to SA383". The badge reads "Not done: the booking was not
changed"; Skye's text is unchanged. (The screenshots were taken after the run; the write-log
line shows "?" where later code says "(no booking code given)", and the ticker shows the
predictor decisions recorded while it was down.)

| File | What |
|---|---|
| `phase_b/screen_light.png`, `phase_b/screen_dark.png` | the Skypiea Air view at the end of that conversation (also `../img/live_demo_*.png`) |
| `phase_b/screen_details.png` | the same, with the Details drawer open |
| `phase_b/export.csv` | per-call CSV (metadata only) |
| `phase_b/turns.csv` | per-turn CSV, including `truth_claims`, `truth_verdict`, `truth_badge`, `truth_note_sent` |
| `phase_b/summary.json` | the console's totals, per-call rows, write log and truth checks |
| `phase_b/session_log.txt` | the conversation as text (fictional customer), calls, hints, write log and truth checks |
