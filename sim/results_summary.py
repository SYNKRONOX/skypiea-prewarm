"""Results package: docs/results_summary.md, built only from committed result files.

    python -m sim.results_summary                          # make -f mk/model.mk results-summary
    python -m sim.results_summary --infra-results results \
        --scenarios 'results/scenario_aws*_calls.csv'      # with the AWS results

Every number in the output is read from a file under results/ (or the infra paths given);
none is typed here. Each input must be tracked by git and unchanged against HEAD, so the page
only shows committed results; the header lists every input with the commit that last changed
it. Missing infra inputs (E0 cold starts, AWS scenario runs) give a row marked **TODO**.

Sections: E1 next-tool accuracy with CIs and calibration (predictors.phase4.report, which
reads p1_accuracy.csv.gz and p4_ranker.csv); service latency (loadtest.csv); warm policies
under kind, AWS without a cap and AWS with the cap (sim_sensitivity.csv.gz); cap sensitivity;
oracle; baselines and FR-2 under AWS with the cap (sim_offline.csv.gz); hideable fractions
(sim_hideable.csv); cold-call decomposition (sim_cold_decomp.csv); the SYNTHETIC prd profile
(sim_prd.csv.gz); E0 cold starts and AWS scenario runs (infra). Scenario cold rates carry a
95% session-level bootstrap CI (1,000 resamples); idle pod-s is one run-level counter, so it
has none. Infra inputs must be committed in their own repo too.
"""

from __future__ import annotations

import argparse
import glob
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
OUT = ROOT / "docs" / "results_summary.md"
INPUTS = ["p1_accuracy.csv.gz", "p4_ranker.csv", "loadtest.csv", "sim_sensitivity.csv.gz",
          "sim_offline.csv.gz", "sim_hideable.csv", "sim_cold_decomp.csv", "sim_prd.csv.gz",
          "sim_choice_coding_cap2.json"]
RUNS = (("kind", "kind"), ("aws", "AWS, no cap"))  # + the capped run, named from its rows
TODO = "**TODO**"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True,
                          text=True).stdout.strip()


def check_committed(paths: list[Path]) -> list[tuple[str, str]]:
    """(path, last commit) for each input; raises if one is untracked or changed."""
    out = []
    for p in paths:
        rel = str(p.relative_to(ROOT))
        if subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=ROOT,
                          capture_output=True).returncode:
            raise SystemExit(f"{rel} is not committed")
        if _git("status", "--porcelain", "--", rel):
            raise SystemExit(f"{rel} differs from HEAD; commit it first")
        out.append((rel, _git("log", "-1", "--format=%h", "--", rel)))
    return out


def committed_elsewhere(path: Path) -> str:
    """Last commit of an input outside this repo (the infra results); raises if the file is
    untracked or changed there."""
    def git(*a: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-C", str(path.parent), *a], capture_output=True,
                              text=True)
    if git("ls-files", "--error-unmatch", path.name).returncode:
        raise SystemExit(f"{path} is not committed")
    if git("status", "--porcelain", "--", path.name).stdout.strip():
        raise SystemExit(f"{path} differs from HEAD of its repo; commit it first")
    return git("log", "-1", "--format=%h", "--", path.name).stdout.strip()


def _read(name: str) -> pd.DataFrame:
    return pd.read_csv(RESULTS / name, keep_default_na=False, low_memory=False)


def _f(x: object, fmt: str = ".1f") -> str:
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "-"
    return "-" if math.isnan(v) else format(v, fmt)


def _wi(r: pd.Series | None, margin: bool = True) -> str:
    if r is None:
        return "-"
    s = f"{_f(r.warm_pct)} / {_f(r.idle_pod_s_per_1k, ',.0f')}"
    if margin and "margin_pts" in r and _f(r.margin_pts) != "-":
        s += f" ({float(r.margin_pts):+.1f})"
    elif margin and "margin_pts" in r:
        s += " (outside KA range)"
    return s


def _table(head: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + \
        ["| " + " | ".join(r) + " |" for r in rows]


# --------------------------------------------------------------------------- sections
def accuracy() -> list[str]:
    from predictors.phase4 import report
    return ["## E1: next-tool accuracy and calibration (TEST)", "",
            "Ours = `Ranker` (LightGBM re-ranker). Built by `predictors.phase4.report()` from "
            "`results/p1_accuracy.csv.gz` and `results/p4_ranker.csv`.", "", report(), ""]


def latency() -> list[str]:
    d = _read("loadtest.csv")
    d = d[~d.model.str.endswith("-dev")]
    rows = []
    for _, r in d.sort_values(["mode", "workers", "rate", "concurrency", "time"]).iterrows():
        load = (f"open loop {_f(r.rate, '.0f')} req/s" if r["mode"] == "open"
                else f"closed loop {r.concurrency} clients")
        rows.append([load, str(r.workers), _f(r.seconds, ".0f"), r.model, _f(r.rps),
                     _f(r.p50_ms), _f(r.p99_ms), str(r.errors), _f(r.service_p99_ms)])
    return ["## Service latency (`results/loadtest.csv`; every run, none picked)", "",
            *_table(["Load", "Workers", "s", "Bundle", "req/s", "p50 ms", "p99 ms", "errors",
                     "service compute p99 ms"], rows), ""]


def _sens() -> tuple[pd.DataFrame, str, str]:
    d = _read("sim_sensitivity.csv.gz")
    capped = sorted(r for r in d.run.unique() if "_cap" in r and "uncapped" not in r)
    two = [r for r in capped if d[d.run == r].scaleup_cap.astype(str).iloc[0] == "2"]
    run = two[0] if two else capped[0]
    x = d[d.run == run].iloc[0]
    return d, run, f"AWS, cap {x.scaleup_cap} + {x.contention}"


def _get(d: pd.DataFrame, **kw: str) -> pd.Series | None:
    m = pd.Series(True, index=d.index)
    for k, v in kw.items():
        m &= d[k].astype(str) == v
    x = d[m]
    return None if x.empty else x.sort_values("time").iloc[-1]


def policies() -> list[str]:
    d, cap_run, cap_name = _sens()
    runs = [*RUNS, (cap_run, cap_name)]
    names = {"keep_alive_30": "keep-alive 30 s", "coding_frozen": "coding, frozen (before)",
             "coding_retuned": "coding, retuned (shipped)", "support_frozen": "support"}
    rows = []
    for src in d.source.unique():
        for mode in ("cached", "uncached"):
            for pol in names:
                cells = [_wi(_get(d, run=r, source=src, cold_mode=mode, policy=pol),
                             pol != "keep_alive_30") for r, _ in runs]
                if any(c != "-" for c in cells):
                    rows.append([src, mode, names[pol], *cells])
    out = ["## Warm policies: frozen vs retuned vs keep-alive (TEST)", "",
           "Cells: warm % of calls / idle pod-s per 1,000 calls (equal-idle margin vs "
           "keep-alive in points, log-linear between keep-alive 5 s and 600 s). Source: "
           "`results/sim_sensitivity.csv.gz`. The retuned coding profile was selected on VAL "
           "and adopted after these TEST margins were seen.", "",
           *_table(["Source", "Images", "Policy", *[n for _, n in runs]], rows), ""]
    caps = sorted({r for r in d.run.unique() if r.startswith("aws_")},
                  key=lambda r: (int(d[d.run == r].scaleup_cap.iloc[0]) or 10 ** 6))
    head = []
    for r in caps:
        x = d[d.run == r].iloc[0]
        head.append(f"cap {x.scaleup_cap}" if int(x.scaleup_cap) else "no cap")
    rows = []
    for src in d.source.unique():
        for pol in names:
            if pol == "keep_alive_30":
                continue
            cells = []
            for r in caps:
                c = [_get(d, run=r, source=src, cold_mode=m, policy=pol)
                     for m in ("cached", "uncached")]
                cells.append(" / ".join("-" if x is None else _f(x.margin_pts, "+.1f")
                                        for x in c))
            if any(c != "- / -" for c in cells):
                rows.append([src, names[pol], *cells])
    cont = d[d.run == caps[0]].contention.iloc[0]
    out += [f"### Sensitivity to the scale-up cap (TEST; AWS, contention {cont}; margin vs "
            "keep-alive, cached / uncached)", "",
            "E0 measured 1 and 3 starts at once, not 2: every overlapping start takes the "
            "3-at-once time. Report only; nothing was tuned on these.", "",
            *_table(["Source", "Policy", *head], rows), ""]
    rows = []
    for src in d.source.unique():
        for mode in ("cached", "uncached"):
            rows.append([src, mode,
                         _wi(_get(d, run="aws", source=src, cold_mode=mode, policy="oracle"),
                             False),
                         _wi(_get(d, run=cap_run, source=src, cold_mode=mode,
                                  policy="oracle"), False),
                         _wi(_get(d, run=cap_run, source=src, cold_mode=mode,
                                  policy="oracle_cap"), False)])
    out += ["### Oracle (TEST; warm % / idle pod-s per 1k)", "",
            *_table(["Source", "Images", "oracle, AWS no cap (ceiling)",
                     f"oracle, {cap_name} (not achievable)",
                     f"cap-aware oracle, {cap_name}"], rows), ""]
    return out


def baselines() -> list[str]:
    from sim.figures import latest
    d = latest()
    _, cap_run, cap_name = _sens()
    d = d[d.run == cap_run]
    pols = ["keep_alive_30", "keep_alive_60", "markov1_top1", "ours_old_tau0.6", "ours_new",
            "oracle", "always_warm"]
    rows = []
    for (src, mode), g in d.groupby(["source", "cold_mode"], sort=False):
        for p in pols:
            r = _get(g, policy=p)
            if r is not None:
                fr2 = str(r.fr2_calls) not in ("", "0")
                rows.append([src, mode, p, _f(r.tau, ".1f") if fr2 else "-", _wi(r, False),
                             _f(r.fr2_call_ready_pct) if fr2 else "-",
                             str(r.fr2_calls) if fr2 else "-",
                             _f(r.fr2_sessions_80_pct) if fr2 else "-",
                             _f(r.prewarm_used_pct)])
    return ["## Baselines and FR-2 (TEST, " + cap_name + "; `results/sim_offline.csv.gz`)",
            "", "`ours_new` here is the profile shipped when the run was made (coding = "
            "coding_frozen). FR-2 columns only for policies with a fixed tau (the tau "
            "column; the PRD target is in docs/prd_excerpts.md, FR-2; support runs at its own "
            "tau): share of "
            "predicted calls whose pod was Ready on arrival, the number of such calls, and "
            "the share of sessions with at least 80% of them ready.", "",
            *_table(["Source", "Images", "Policy", "tau", "warm % / idle per 1k", "FR-2 ready %",
                     "FR-2 calls", "sessions >=80% ready", "prewarms used %"], rows), ""]


def hideable() -> list[str]:
    from sim.figures import run_tag
    d = _read("sim_hideable.csv")
    d = d[d.tool_class == "ALL"].copy()
    d["run"] = [run_tag(*v) for v in zip(d.cold_start_label, d.scaleup_cap, d.contention,
                                         strict=True)]
    runs = [r for r in ("kind", "aws") if r in set(d.run)]
    rows = []
    for (src, mode), g in d.groupby(["source", "cold_mode"], sort=False):
        rows.append([src, mode, *[_f(_get(g, run=r).hideable_pct) if _get(g, run=r)
                                  is not None else "-" for r in runs]])
    return ["## Hideable calls (TEST; share whose think gap >= the server's cold start)", "",
            "Source: `results/sim_hideable.csv` (depends only on the cold-start file, not on "
            "the cap).", "", *_table(["Source", "Images", *runs], rows), ""]


def decomposition() -> list[str]:
    d = _read("sim_cold_decomp.csv")
    d = d[d.contention.astype(str) != ""]  # the AWS + cap rows (newest run)
    x = d.iloc[-1]
    rows = [[r.source, r.profile, r.cause, _f(r.pct_of_calls), _f(r.ours_warms_pct),
             f"{_f(r.ka30_idle_per_1k, ',.0f')} -> {_f(r.ours_idle_per_1k, ',.0f')}"]
            for _, r in d.iterrows()]
    return [f"## Cold calls left by keep-alive 30 s, by cause (TEST, {x.cold_mode}, AWS, "
            f"cap {x.scaleup_cap} + {x.contention}; frozen profiles)", "",
            "Source: `results/sim_cold_decomp.csv`. Analysis only; nothing was tuned on it.",
            "", *_table(["Source", "Profile", "Cause", "% of all calls", "share ours warms %",
                         "idle /1k: KA30 -> ours"], rows), ""]


def synthetic() -> list[str]:
    d = _read("sim_prd.csv.gz")
    rows = []
    for mode in ("cached", "uncached"):
        for p in ("keep_alive_30", "markov1_top1", "ours_new", "oracle"):
            r = _get(d, cold_mode=mode, policy=p)
            if r is not None:
                rows.append([mode, "prd" if p == "ours_new" else p, _wi(r, False)])
    return ["## SYNTHETIC prd profile (held-out synthetic TEST; NOT evidence about real "
            "workloads)", "", "Source: `results/sim_prd.csv.gz` (traces sampled from the PRD "
            "scenario graphs).", "", *_table(["Images", "Policy", "warm % / idle per 1k"],
                                             rows), ""]


def e0(infra: Path | None) -> list[str]:
    head = ["## E0 cold starts (infra)", ""]
    files = {} if infra is None else {
        k: infra / f"e0_{k}_summary.csv" for k in ("aws", "aws_p3", "kind", "kind_p3")}
    if infra is None or not files["aws"].exists():
        return head + _table(["Item", "Status"], [[
            "E0 per-tool cold starts (results/e0_*_summary.csv on feat/infra)",
            f"{TODO}: pass --infra-results <path> after the merge"]]) + [""]
    rows = []
    s = {k: pd.read_csv(p, keep_default_na=False) for k, p in files.items() if p.exists()}
    commits = ", ".join(f"`{p.name}` {committed_elsewhere(p)}" for p in files.values()
                        if p.exists())
    for _, r in s["aws"].iterrows():
        p3 = s.get("aws_p3")
        q = None if p3 is None else p3[(p3.tool == r.tool) & (p3["mode"] == r["mode"])]
        k = s.get("kind")
        kk = None if k is None else k[(k.tool == r.tool) & (k["mode"] == r["mode"])]
        rows.append([r.tool, r.tier, r["mode"], str(r.n), _f(r.total_median_ms, ".0f"),
                     _f(r.total_p95_ms, ".0f"),
                     "-" if q is None or q.empty else _f(q.total_median_ms.iloc[0], ".0f"),
                     "-" if kk is None or kk.empty else _f(kk.total_median_ms.iloc[0], ".0f")])
    commit = s["aws"].git_commit.iloc[0]
    return head + [f"Source: `{files['aws']}` (E0 commit {commit}) and the parallel-3 and kind "
                   "summaries next to it. Files and last commits: " + commits + ".", "",
                   *_table(["Tool", "Tier", "Images", "n", "AWS median ms", "AWS p95 ms",
                            "AWS 3 at once median ms", "kind median ms"], rows), ""]


BOOT_N, BOOT_SEED = 1000, 0


def session_ci(d: pd.DataFrame, n: int = BOOT_N, seed: int = BOOT_SEED) -> tuple[float, float]:
    """95% percentile CI of the cold rate (cold calls / calls), resampling sessions."""
    g = d.groupby("session").cold_b.agg(["sum", "size"])
    cold, calls = g["sum"].to_numpy(float), g["size"].to_numpy(float)
    idx = np.random.default_rng(seed).integers(0, len(g), (n, len(g)))
    rate = cold[idx].sum(1) / calls[idx].sum(1)
    lo, hi = np.percentile(rate, [2.5, 97.5])
    return float(lo), float(hi)


def scenarios(pattern: str | None) -> list[str]:
    """Replayer runs: one row per (dataset, scenario, policy, log), from the per-call CSVs
    (``*_calls.csv``) and the run summaries next to them (``*_summary.csv``)."""
    head = ["## AWS scenario runs (infra replayer)", ""]
    paths = sorted(glob.glob(str(Path(pattern).expanduser()))) if pattern else []
    if not paths:
        return head + _table(["Item", "Status"], [[
            "Scenario A/B/C runs on AWS (replayer CSVs on feat/infra)",
            f"{TODO}: pass --scenarios '<glob of *_calls.csv>' after the merge"]]) + [""]
    d = pd.concat([pd.read_csv(p, keep_default_na=False) for p in paths], ignore_index=True)
    need = {"dataset", "scenario", "policy", "session", "cold", "added_ms", "log"}
    if not need <= set(d.columns):
        return head + [f"{TODO}: {len(paths)} files but columns {sorted(need)} not found", ""]
    sums = [Path(p.replace("_calls.csv", "_summary.csv")) for p in paths]
    commits = ", ".join(f"`{Path(p).name}` {committed_elsewhere(Path(p))}"
                        for p in [*paths, *map(str, sums)] if Path(p).exists())
    sm = pd.concat([pd.read_csv(p, keep_default_na=False) for p in sums if p.exists()],
                   ignore_index=True) if any(p.exists() for p in sums) else pd.DataFrame()
    if not sm.empty:
        sm = sm[sm.error == ""]
    d["cold_b"] = d.cold.astype(str).str.lower().isin(["true", "1"])
    d["added"] = pd.to_numeric(d.added_ms, errors="coerce")
    keys = ["dataset", "scenario", "policy", "log"]
    rows, check = [], []
    for k, g in sorted(d.groupby(keys), key=lambda kg: (kg[0][0] != "real", kg[0])):
        lo, hi = session_ci(g)
        r = sm[(sm[keys] == pd.Series(k, index=keys)).all(axis=1)] if not sm.empty else sm
        idle = "-" if r.empty else _f(r.idle_pod_s.iloc[0], ",.0f")
        p50, p99 = ((_f(g.added.quantile(q), ".0f") for q in (0.5, 0.99)) if r.empty else
                    (_f(r[c].iloc[0], ".0f") for c in ("added_ms_p50", "added_ms_p99")))
        if not r.empty and int(float(r.cold.iloc[0])) != int(g.cold_b.sum()):
            check.append(f"{'/'.join(k)}: {int(g.cold_b.sum())} cold calls in the call file, "
                         f"{r.cold.iloc[0]} in the summary")
        rows.append([*k[:3], str(g.session.nunique()), str(len(g)),
                     f"{_f(100 * g.cold_b.mean())} [{_f(100 * lo)}, {_f(100 * hi)}]",
                     idle, "n/a (one run-level counter)",
                     p50, p99])
    n_sess = d.groupby(["dataset", "scenario"]).session.nunique()
    note = "; ".join(f"{ds} {sc}: {n} sessions" for (ds, sc), n in n_sess.items())
    out = head + [
        f"Source: {len(paths)} files matching `{pattern}` and the `*_summary.csv` next to "
        f"them ({commits}). Sessions per scenario: {note}. With so few sessions the CIs are "
        "wide. SYNTHETIC rows replay traces sampled from the PRD graphs, not real sessions; "
        "added p50/p99 are the run summaries' values.", "",
        f"Cold rate: cold calls / calls, with a 95% session-level bootstrap CI ({BOOT_N:,} "
        f"resamples of sessions with replacement, seed {BOOT_SEED}). Idle pod-s is the "
        "controller's `skypiea_pod_seconds_idle_total` over the whole run; pods are shared by "
        "all sessions of a run, so it cannot be split by session and has no session CI.", "",
        *_table(["Dataset", "Scenario", "Policy", "sessions", "calls", "cold % [95% CI]",
                 "idle pod-s", "idle CI", "added p50 ms", "added p99 ms"], rows), ""]
    if check:
        out += ["Mismatch between call files and summaries:", "", *[f"- {c}" for c in check], ""]
    return out


def build(infra: Path | None, pattern: str | None) -> str:
    ins = check_committed([RESULTS / n for n in INPUTS])
    out = ["# Results summary (generated)", "",
           "Generated by `python -m sim.results_summary` at commit "
           f"{_git('rev-parse', '--short', 'HEAD')}. Do not edit by hand: every number comes "
           "from the inputs below. Limits: "
           "docs/limits.md; where each spoken number comes from: docs/numbers_and_sources.md.",
           "", *_table(["Input", "last changed in"], [[f"`{p}`", c] for p, c in ins]), ""]
    for part in (accuracy(), latency(), policies(), baselines(), hideable(), decomposition(),
                 synthetic(), e0(infra), scenarios(pattern)):
        out += part
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--infra-results", default=None, help="feat/infra results/ (E0 summaries)")
    p.add_argument("--scenarios", default=None, help="glob of AWS scenario replayer CSVs")
    p.add_argument("--out", default=str(OUT))
    args = p.parse_args(argv)
    infra = Path(args.infra_results).expanduser() if args.infra_results else None
    Path(args.out).write_text(build(infra, args.scenarios), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
