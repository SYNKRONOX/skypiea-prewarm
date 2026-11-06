"""Lookahead 1 vs 2 in the offline simulator (feat/console-leadtime).

    nice -n 10 python -m sim.lookahead --split val  --data ~/skypiea-prewarm/data/processed
    nice -n 10 python -m sim.lookahead --split test --data ...      # once, after the VAL decision

Setting: the profiles' own sources (support: tau2_bench; coding: tracelab, copilot_2026), cached
images, AWS cold starts (configs/cold_start.yaml), at most 2 scale-ups at once per node with
parallel_3 contention (configs/model/sim.yaml) - the setting the shipped coding profile was
retuned in.

For each profile and lookahead in {1, 2} the policy runs over the threshold sweep of sim.yaml
(tau and lambda) with everything else as shipped; keep-alive runs at 5..600 s. Equal-idle
margin (as in sim/retune.py): a point's warm % minus the comparison curve's warm % at the same
idle pod-seconds per 1,000 calls, log-linear between the curve's points; NaN outside its range.
Reported at the profile's SHIPPED threshold (support tau 0.4, coding lambda 100):
  la1 vs keep-alive, la2 vs keep-alive, la2 vs the la1 curve (all thresholds of la1),
averaged over the profile's sources, with 95% CIs from 1,000 session bootstrap resamples
(sessions resampled with replacement within each source; seed 0). Sessions are independent in
this simulator (own pods, own node), so per-session sums are exact.

Pre-registered rule (docs/handoff/STATE_infra.md, before any result): adopt lookahead 2 as a
labelled profile variant only if la2-vs-la1 > 0 with the CI above 0 AND la2-vs-keep-alive >=
la1-vs-keep-alive, on VAL. TEST is run once afterwards and reported regardless.

Writes results/sim_lookahead_<split>.json and .md (generated; nothing typed in).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from predictors.cold_start import ColdStarts
from predictors.decide import DecideConfig, load_pricing, load_profiles
from predictors.eval import RESULTS, git_commit
from sim.offline import ROOT, Env, Stats, baseline_specs, load_config, ours_spec, prepare, simulate
from sim.tune import thresholds

KEEP = ("keep_alive_5", "keep_alive_10", "keep_alive_30", "keep_alive_60", "keep_alive_300",
        "keep_alive_600")
SHIPPED = {"support": "tau0.4", "coding": "lam100"}
B = 1000


def per_session(prep, spec, env) -> np.ndarray:
    """(sessions, 3): calls, warm calls, idle ms - one independent simulation per session."""
    out = np.zeros((len(prep.eps), 3))
    for i, ep in enumerate(prep.eps):
        st = Stats()
        simulate(ep, spec, env, prep.servers, st)
        out[i] = (st.calls, st.warm, st.idle_ms)
    return out


def point(m: np.ndarray) -> tuple[float, float]:
    """(warm %, idle pod-s per 1,000 calls) of summed per-session rows."""
    calls, warm, idle = m.sum(axis=0)
    return 100.0 * warm / max(calls, 1), idle / 1000.0 * 1000.0 / max(calls, 1)


def margin(p: tuple[float, float], curve: list[tuple[float, float]]) -> float:
    pts = sorted(curve, key=lambda v: v[1])
    lx = np.log(np.clip([q[1] for q in pts], 1, None))
    x = math.log(max(p[1], 1.0))
    if x < lx.min() or x > lx.max():
        return math.nan
    return p[0] - float(np.interp(x, lx, [q[0] for q in pts]))


def evaluate(per: dict[str, dict[str, np.ndarray]], shipped: str, idx=None) -> dict[str, float]:
    """Margins at the shipped threshold, mean over sources (idx: bootstrap indices)."""
    res: dict[str, list[float]] = {"la1_vs_ka": [], "la2_vs_ka": [], "la2_vs_la1": []}
    for src, pols in per.items():
        sel = (lambda m: m) if idx is None else (lambda m, i=idx[src]: m[i])
        pt = {k: point(sel(v)) for k, v in pols.items()}
        ka = [pt[k] for k in KEEP]
        la1 = [v for k, v in pt.items() if k.startswith("la1|")]
        res["la1_vs_ka"].append(margin(pt[f"la1|{shipped}"], ka))
        res["la2_vs_ka"].append(margin(pt[f"la2|{shipped}"], ka))
        res["la2_vs_la1"].append(margin(pt[f"la2|{shipped}"], la1))
    return {k: float(np.mean(v)) for k, v in res.items()}


def run(split: str, data: Path | None, profiles: list[str]) -> dict[str, Any]:
    sim = load_config()
    cold = ColdStarts.load(ROOT / sim["cold_start_path"])
    pricing = load_pricing()
    srcs = {s["source"]: s for s in sim["sources"]}
    out: dict[str, Any] = {"split": split, "cold_start": cold.label,
                           "cold_start_sha256": cold.sha256, "git_commit": git_commit(),
                           "scaleup_cap": sim["max_concurrent_scale_ups"],
                           "contention": sim["contention"], "cold_mode": "cached",
                           "bootstrap": {"resamples": B, "seed": 0, "unit": "session"},
                           "profiles": {}}
    rng = np.random.default_rng(0)
    for prof in profiles:
        base = DecideConfig.for_profile(prof)
        sources = [s for s in load_profiles()[prof]["sources"] if s in srcs]
        per: dict[str, dict[str, np.ndarray]] = {}
        table: dict[str, dict[str, list[float]]] = {}
        for s in sources:
            t0 = time.time()
            prep = prepare(srcs[s], cold, split, data)
            env = Env(cold, "cached", sim, pricing, base)
            specs = [x for x in baseline_specs(sim, prep, env) if x.name in KEEP]
            for la in (1, 2):
                specs += [ours_spec(f"la{la}|{tl}", prep, env, base.with_(**tk, lookahead=la))
                          for tl, tk in thresholds(sim)]
            per[s] = {sp.name: per_session(prep, sp, env) for sp in specs}
            table[s] = {k: list(point(v)) for k, v in per[s].items()}
            print(f"{prof} {s} {split}: {len(prep.eps)} sessions, {len(specs)} policies, "
                  f"{time.time() - t0:.0f}s", flush=True)
        est = evaluate(per, SHIPPED[prof])
        boots = [evaluate(per, SHIPPED[prof], {s: rng.integers(0, len(next(iter(v.values()))),
                                                                len(next(iter(v.values()))))
                                                 for s, v in per.items()})
                 for _ in range(B)]
        ci = {}
        for k in est:
            xs = np.array([b[k] for b in boots])
            ok = xs[~np.isnan(xs)]
            ci[k] = {"est": est[k], "lo": float(np.percentile(ok, 2.5)) if len(ok) else None,
                     "hi": float(np.percentile(ok, 97.5)) if len(ok) else None,
                     "valid_resamples": int(len(ok))}
        adopt = (ci["la2_vs_la1"]["lo"] is not None and ci["la2_vs_la1"]["est"] > 0
                 and ci["la2_vs_la1"]["lo"] > 0
                 and est["la2_vs_ka"] >= est["la1_vs_ka"])
        out["profiles"][prof] = {"sources": sources, "shipped_threshold": SHIPPED[prof],
                                 "sessions": {s: int(len(next(iter(v.values()))))
                                              for s, v in per.items()},
                                 "points": table, "margins": ci,
                                 "rule_adopt_lookahead2": bool(adopt)}
    return out


def fmt(x: float | None) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.2f}"


def markdown(r: dict[str, Any]) -> str:
    lines = [f"# Lookahead 1 vs 2, simulator, {r['split'].upper()} split", "",
             f"Cold starts: {r['cold_start']} (sha {r['cold_start_sha256']}), cached images, "
             f"at most {r['scaleup_cap']} scale-ups at once per node, contention "
             f"{r['contention']}. Commit {r['git_commit']}. Generated by `python -m "
             f"sim.lookahead --split {r['split']}`.", "",
             "Equal-idle margins in warm-percentage points at the profile's shipped threshold, "
             "mean over its sources; 95% CI from 1,000 session bootstrap resamples.", "",
             "| profile | sources (sessions) | lookahead 1 vs keep-alive | lookahead 2 vs "
             "keep-alive | lookahead 2 vs lookahead-1 curve | adopt lookahead 2 (rule) |",
             "|---|---|---|---|---|---|"]
    for p, d in r["profiles"].items():
        m = d["margins"]

        def cell(k: str, m: dict = m) -> str:
            c = m[k]
            return (f"{fmt(c['est'])} [{fmt(c['lo'])}, {fmt(c['hi'])}]"
                    + (f" ({c['valid_resamples']}/1000 in range)"
                       if c["valid_resamples"] < 1000 else ""))
        srcs = ", ".join(f"{s} ({n})" for s, n in d["sessions"].items())
        lines.append(f"| {p} | {srcs} | {cell('la1_vs_ka')} | {cell('la2_vs_ka')} | "
                     f"{cell('la2_vs_la1')} | {'yes' if d['rule_adopt_lookahead2'] else 'no'} |")
    lines += ["", "Points at the shipped threshold (warm % / idle pod-s per 1,000 calls):", "",
              "| profile | source | lookahead 1 | lookahead 2 | keep-alive 30 s |",
              "|---|---|---|---|---|"]
    for p, d in r["profiles"].items():
        th = d["shipped_threshold"]
        for s, pts in d["points"].items():
            a, b, k = pts[f"la1|{th}"], pts[f"la2|{th}"], pts["keep_alive_30"]
            lines.append(f"| {p} | {s} | {a[0]:.1f} / {a[1]:,.0f} | {b[0]:.1f} / {b[1]:,.0f} | "
                         f"{k[0]:.1f} / {k[1]:,.0f} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["val", "test"], required=True)
    ap.add_argument("--data", type=Path, default=None, help="data/processed root (read-only)")
    ap.add_argument("--profiles", default="support,coding")
    a = ap.parse_args(argv)
    r = run(a.split, a.data.expanduser() if a.data else None, a.profiles.split(","))
    r["data_root"] = str(a.data) if a.data else "data/processed"
    (RESULTS / f"sim_lookahead_{a.split}.json").write_text(json.dumps(r, indent=1) + "\n")
    md = markdown(r)
    (RESULTS / f"sim_lookahead_{a.split}.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
