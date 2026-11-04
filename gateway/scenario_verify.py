"""Turn the overnight Job logs into results files and verify them (no measured number edited).

    python -m gateway.scenario_verify --logs ~/.cache/skypiea/scenario/logs

Writes
  results/scenario_aws_{calls,summary}.csv     real traces (A tau2 support, B TraceLab coding)
  results/scenario_aws_prd_SYNTHETIC_{calls,summary}.csv   PRD scenarios, prd (SYNTHETIC)
  results/concurrent40_aws.csv                             40-session check, real predictor
Every attempt of every Job is kept (column `log`, e.g. B_stub_a2.log). Logs written before
rows carried image tags get them from IMAGES (the deploy record of this session).

Checks: (a) expected calls per policy (A 116, B 319, PRD A/B from the session record,
40-session 945); (b) ok on every row, else listed; (c) image tags on every row; (d)
dataset=SYNTHETIC on every PRD row and only there; (e) no account id (env SKYPIEA_ACCT, never
printed) or key-like string in the written files; (f) every file under 1 MB.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

from gateway.scenario_report import CALL_FIELDS, SUM_FIELDS, parse, write

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "results"
# logs from before the image fields existed (deploy record, this session)
IMAGES = {"A_stub.log": ("predictor:aabdfd2a9a39", "platform:b652e6b"),
          "A_pred.log": ("predictor:aabdfd2a9a39", "platform:b652e6b")}
for k in ("B_stub_a1.log",):
    IMAGES[k] = ("predictor:aabdfd2a9a39", "platform:b652e6b")
POLICIES = ["scale_to_zero", "always_warm", "keep_alive", "markov1", "predictive"]
KEY_RE = re.compile(r"AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|aws_secret_access_key")


def expected() -> dict[tuple[str, str], int]:
    real = json.loads((RES / "scenario_sessions.json").read_text())["scenarios"]
    prd = json.loads((RES / "scenario_sessions_prd_SYNTHETIC.json").read_text())["scenarios"]
    return {("real", "A"): sum(real["A"]["calls"]), ("real", "B"): sum(real["B"]["calls"]),
            ("SYNTHETIC", "A"): sum(prd["A"]["calls"]),
            ("SYNTHETIC", "B"): sum(prd["B"]["calls"])}


def conc(paths: list[Path]) -> list[dict]:
    rows = []
    pat = re.compile(r"^== (\w+): (\d+) calls, ok (\d+), cold (\d+), wall (\d+)s; max starting "
                     r"seen (\d+) \(limit (\d+)\), max queued (\d+), polls (\d+) \(errors (\d+)\)")
    for p in paths:
        cur = None
        for ln in p.read_text().splitlines():
            if m := pat.match(ln):
                cur = dict(zip(["policy", "calls", "ok", "cold", "wall_s", "max_starting",
                                "limit", "max_queued", "polls", "poll_errors"], m.groups(),
                               strict=True))
                cur["log"] = p.name
                rows.append(cur)
            elif cur and ln.strip().startswith("controller:"):
                for k, v in re.findall(r"(queued|dequeued|expired in queue|refused by caps|"
                                       r"scale errors) (\d+)", ln):
                    cur[k.replace(" ", "_")] = v
            elif cur and ln.strip().startswith("gateway:"):
                for k, v in re.findall(r"(predictor calls|predictor errors|prewarm hits|"
                                       r"misses|not ready) (\d+)", ln):
                    cur[k.replace(" ", "_")] = v
            elif cur and ln.strip().startswith("check:"):
                cur.setdefault("checks", []).append(ln.strip()[7:])
            elif ln.startswith("FAILURES:"):
                for r in rows:
                    if r["log"] == p.name:
                        r["failures"] = ln[10:]
    for r in rows:
        r["checks"] = "; ".join(r.get("checks", []))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", type=Path, required=True)
    ap.add_argument("--conc-images", default="predictor:af000231bc7b,platform:b652e6b",
                    help="images of the 40-session Job (its log prints no image line)")
    a = ap.parse_args()
    L = a.logs
    real = sorted([L / "A_stub.log", L / "A_pred.log", *L.glob("B_stub_a*.log"),
                   *L.glob("B_pred_a*.log")])
    prd = sorted([*L.glob("PRDA_*_a*.log"), *L.glob("PRDB_*_a*.log")])
    problems: list[str] = []
    exp = expected()
    out = {}
    for name, logs, ds in (("scenario_aws", real, "real"),
                           ("scenario_aws_prd_SYNTHETIC", prd, "SYNTHETIC")):
        logs = [p for p in logs if p.exists()]
        calls, summ = parse(logs)
        for r in calls + summ:
            if not r.get("predictor_image") and r["log"] in IMAGES:
                r["predictor_image"], r["platform_image"] = IMAGES[r["log"]]
        # (c) tags, (d) dataset
        for r in calls + summ:
            if not r.get("predictor_image") or not r.get("platform_image"):
                problems.append(f"(c) {r['log']}: row without image tags")
                break
        bad_ds = {r.get("dataset") for r in calls + summ} - {ds}
        if bad_ds:
            problems.append(f"(d) {name}: dataset values {bad_ds} (expected only {ds})")
        # (a) expected calls and (b) ok: at least one attempt per (scenario, policy) complete
        for scen in ("A", "B"):
            for pol in POLICIES:
                att = [s for s in summ if s["scenario"] == scen and s["policy"] == pol]
                n_exp = exp[(ds, scen)]
                good = [s for s in att if not s.get("error") and s.get("calls") == n_exp
                        and s.get("ok") == n_exp]
                rows = [c for c in calls if c["scenario"] == scen and c["policy"] == pol]
                if not good:
                    seen = [(s.get("calls"), s.get("ok"), s.get("error")) for s in att]
                    problems.append(f"(a/b) {ds} {scen} {pol}: no complete attempt ({seen})")
                n_fail = sum(1 for c in rows if not c["ok"])
                if n_fail:
                    problems.append(f"(b) {ds} {scen} {pol}: {n_fail} rows ok=False "
                                    f"(kept, attempts {sorted({c['log'] for c in rows})})")
        write(RES / f"{name}_calls.csv", CALL_FIELDS, calls)
        write(RES / f"{name}_summary.csv", SUM_FIELDS, summ)
        out[name] = (len(calls), len(summ))
    cl = sorted(L.glob("conc40_a*.log"))
    crow = conc(cl)
    pred_img, plat_img = a.conc_images.split(",")
    for r in crow:
        r["predictor_image"], r["platform_image"], r["dataset"] = pred_img, plat_img, "real"
    fields = ["dataset", "predictor_image", "platform_image", "policy", "calls", "ok", "cold",
              "wall_s", "max_starting", "limit", "max_queued", "polls", "poll_errors", "queued",
              "dequeued", "expired_in_queue", "refused_by_caps", "scale_errors",
              "predictor_calls", "predictor_errors", "prewarm_hits", "misses", "not_ready",
              "checks", "failures", "log"]
    with (RES / "concurrent40_aws.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(crow)
    if not any(r["calls"] == "945" and r["ok"] == "945" for r in crow):
        problems.append(f"(a/b) 40-session: no attempt with 945/945 ok "
                        f"({[(r['calls'], r['ok']) for r in crow]})")
    # (e) account id / keys, (f) size
    acct = os.environ.get("SKYPIEA_ACCT", "")
    files = [RES / f"{n}_{k}.csv" for n in out for k in ("calls", "summary")] + [
        RES / "concurrent40_aws.csv", RES / "scenario_sessions.json",
        RES / "scenario_sessions_prd_SYNTHETIC.json", RES / "concurrent_sessions.json"]
    for p in files:
        t = p.read_text()
        if (acct and acct in t) or KEY_RE.search(t):
            problems.append(f"(e) {p.name}: account id or key-like string")
        if p.stat().st_size >= 1_000_000:
            problems.append(f"(f) {p.name}: {p.stat().st_size} bytes (>= 1 MB)")
    if not acct:
        problems.append("(e) SKYPIEA_ACCT not set: account-id check not run")
    for n, (c, s) in out.items():
        print(f"{n}: {c} call rows, {s} policy lines")
    print(f"concurrent40_aws: {len(crow)} attempt lines from {[p.name for p in cl]}")
    print("PROBLEMS:\n  " + "\n  ".join(problems) if problems else "VERIFY: all checks pass")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
