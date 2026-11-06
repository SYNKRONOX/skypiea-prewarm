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
