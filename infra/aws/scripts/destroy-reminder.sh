#!/usr/bin/env bash
# Write a calendar file (.ics) with a reminder to run `make aws-destroy`, H hours from now.
# Import it into your calendar (Google: Settings > Import; Outlook/Apple: open the file).
# The file goes to your home directory, never into the repo.
set -euo pipefail
H="${1:-8}"
OUT="${REMINDER_OUT:-$HOME/skypiea-aws-destroy-reminder.ics}"
python3 - "$H" "$OUT" <<'PY'
import datetime as dt, sys, uuid
h, out = float(sys.argv[1]), sys.argv[2]
now = dt.datetime.now(dt.timezone.utc)
at = now + dt.timedelta(hours=h)
f = lambda t: t.strftime("%Y%m%dT%H%M%SZ")
rate = 0.10825  # USD/h running, docs/aws_plan.md (m7i-flex.large + public IPv4 + 20 GB gp3)
ics = "\r\n".join([
    "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//skypiea//aws-reminder//EN",
    "BEGIN:VEVENT", f"UID:{uuid.uuid4()}@skypiea", f"DTSTAMP:{f(now)}",
    f"DTSTART:{f(at)}", f"DTEND:{f(at + dt.timedelta(minutes=30))}",
    "SUMMARY:Skypiea AWS: run make aws-destroy",
    f"DESCRIPTION:The Skypiea VM costs about ${rate:.3f}/h (${rate*24:.2f}/day) while it runs. "
    "Back up results (aws s3 sync)\\, then: make aws-destroy",
    "BEGIN:VALARM", "TRIGGER:-PT0M", "ACTION:DISPLAY", "DESCRIPTION:Skypiea AWS destroy", "END:VALARM",
    "END:VEVENT", "END:VCALENDAR", ""])
open(out, "w").write(ics)
print(f"reminder at {at.astimezone().strftime('%Y-%m-%d %H:%M %Z')} -> {out} (import it into your calendar)")
PY
