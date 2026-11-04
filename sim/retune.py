"""Coding-profile retune under the scale-up cap (VAL only), cap sensitivity and final tables.

    nice -n 10 python -m sim.retune --aws PATH --kind PATH            # tune, then sens
    nice -n 10 python -m sim.retune --aws PATH --kind PATH --steps sens

tune (VAL split, cached images, AWS cold starts, the cap and contention of sim.yaml: at most 2
scale-ups at once per node, parallel_3): the scale-up cap was unknown when the profiles were
frozen, so the CODING profile got one retune with the cap in the loop. Candidates change only
how many pods one decision starts at once: top-k warm tools per decision (max_warm_tools),
one copy per tool, staggered starts (stagger_ms), and higher thresholds (tau up to 0.95 and the
lambda sweep). The winner was adopted as ``coding``; the tune starts from ``coding_frozen``
(configs/model/profiles.yaml, frozen_profiles). Every variant x threshold goes to
results/sim_variants.csv.gz (step ``cap2_retune``). The support profile is not touched.

Selection: the frozen rule (max mean warm % s.t. idle <= 30% of always-warm) does not bind for
coding (the frozen pick used 5.9% of always-warm), so it picks whatever warms most, which is
what the cap punishes. The retune instead picks, among settings within that idle budget on
every source, the largest mean VAL equal-idle margin over keep-alive (keep-alive warm % at the
same idle pod-s, log-linear between keep-alive points; a setting outside the keep-alive range
on a source is not eligible). What the old rule would pick is recorded too.

sens (TEST split, both image modes): keep-alive, the frozen profile, the retuned coding
profile (coding sources), the support profile (tau2), the oracle and the cap-aware oracle under
kind, AWS (no cap, no contention) and AWS with parallel_3 contention at cap 1, 2, 3 and no cap.
E0 measured 1 and 3 starts at once, not 2: every capped run uses the 3-at-once time for any
overlapping start. Rows: results/sim_sensitivity.csv.gz; tables: results/sim_sensitivity.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

from predictors.cold_start import ColdStarts
from predictors.decide import DecideConfig, load_pricing, load_profiles, profile_of
from predictors.eval import RESULTS, git_commit
from sim.offline import (
    Env,
    Spec,
    _append,
    _open,
    baseline_specs,
    load_config,
    nfr3,
    ours_spec,
    prepare,
    run_specs,
)
from sim.tune import Tuner, thresholds

CHOICE = RESULTS / "sim_choice_coding_cap2.json"
SENS_CSV = RESULTS / "sim_sensitivity.csv.gz"
SENS_MD = RESULTS / "sim_sensitivity.md"
KEEP = ("keep_alive_5", "keep_alive_10", "keep_alive_30", "keep_alive_60", "keep_alive_300",
        "keep_alive_600")
STEP = "cap2_retune"
VARIANTS: list[tuple[str, dict[str, Any]]] = [
    ("frozen", {}),
    ("top1", {"max_warm_tools": 1}),
    ("top2", {"max_warm_tools": 2}),
    ("copies1", {"max_copies_per_tool": 1}),
    ("top1,copies1", {"max_warm_tools": 1, "max_copies_per_tool": 1}),
    ("top2,copies1", {"max_warm_tools": 2, "max_copies_per_tool": 1}),
    ("copies1,stagger3s", {"max_copies_per_tool": 1, "stagger_ms": 3000.0}),
    ("copies1,stagger6s", {"max_copies_per_tool": 1, "stagger_ms": 6000.0}),
    ("top2,copies1,stagger6s", {"max_warm_tools": 2, "max_copies_per_tool": 1,
                                "stagger_ms": 6000.0}),
]


def ka_margin(warm: float, idle: float, ka: dict[str, tuple[float, float]]) -> float:
    """warm minus keep-alive warm % at the same idle (log-linear; NaN outside the range)."""
    pts = sorted(ka.values(), key=lambda v: v[1])
    lx = np.log(np.clip([p[1] for p in pts], 1, None))
    x = math.log(max(idle, 1.0))
    if x < lx.min() or x > lx.max():
        return math.nan
    return warm - float(np.interp(x, lx, [p[0] for p in pts]))


def _ka(rows: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
    return {r["policy"]: (float(r["warm_pct"]), float(r["idle_pod_s_per_1k"]))
            for r in rows if r["policy"] in KEEP}


def tune(aws: ColdStarts) -> dict[str, Any]:
    sim = load_config()
    sim = {**sim, "tau_sweep": [*sim["tau_sweep"], 0.95]}
    prof = load_profiles()["coding"]
    preps = [prepare(s, aws, "val") for s in sim["sources"] if s["source"] in prof["sources"]]
    base = DecideConfig.for_profile("coding_frozen")
    t = Tuner(preps, aws, sim, base, "coding")
    ka: dict[str, dict[str, tuple[float, float]]] = {}
    for prep in preps:  # the Tuner's baseline rows (same env, same split); rerun is cheap
        ka[prep.source] = _ka(run_specs(prep, [s for s in baseline_specs(sim, prep, t.env)
                                               if s.name in KEEP], t.env, sim))
    cands: list[dict[str, Any]] = []
    for name, over in VARIANTS:
        per: dict[str, dict[str, dict[str, Any]]] = {}
        for prep in preps:
            specs = [ours_spec(f"{name}|{tl}", prep, t.env, base.with_(**over, **tk))
                     for tl, tk in thresholds(sim)]
            rows = run_specs(prep, specs, t.env, sim, {"step": STEP, "variant": name,
                                                      "profile": "coding", **t.scaleup})
            t._write(rows, prep.source)
            for (tl, _), r in zip(thresholds(sim), rows, strict=True):
                per.setdefault(tl, {})[prep.source] = r
        for tl, tk in thresholds(sim):
            rs = per[tl]
            ps = {s: {"warm_pct": float(r["warm_pct"]),
                      "idle_pod_s_per_1k": float(r["idle_pod_s_per_1k"]),
                      "idle_over_always_warm": float(r["idle_over_always_warm"]),
                      "margin_pts": ka_margin(float(r["warm_pct"]),
                                              float(r["idle_pod_s_per_1k"]), ka[s]),
                      "scaleup_queued_pct": float(r["scaleup_queued_pct"] or 0)}
                  for s, r in rs.items()}
            m = [v["margin_pts"] for v in ps.values()]
            cands.append({"variant": name, "overrides": over, "threshold": tl,
                          "threshold_cfg": tk,
                          "feasible": max(v["idle_over_always_warm"] for v in ps.values())
                          <= t.ratio,
                          "mean_warm": sum(v["warm_pct"] for v in ps.values()) / len(ps),
                          "mean_margin": math.nan if any(math.isnan(x) for x in m)
                          else sum(m) / len(m), "per_source": ps})
    ok = [c for c in cands if c["feasible"] and not math.isnan(c["mean_margin"])]
    win = max(ok, key=lambda c: (c["mean_margin"], c["mean_warm"]))
    old_rule = max((c for c in cands if c["feasible"]), key=lambda c: c["mean_warm"])
    frozen = next(c for c in cands if c["variant"] == "frozen" and c["threshold"] == "lam100")
    log = {"split": "val", "cold_mode": "cached", "cold_start": aws.label,
           "cold_start_sha256": aws.sha256, "scaleup_cap": t.env.cap,
           "contention": t.env.contention, "git_commit": t.commit,
           "rule": "max mean VAL equal-idle margin vs keep-alive among settings with idle <= "
                   f"{t.ratio} x always-warm on every source",
           "keep_alive_val": ka, "chosen": {**win, "decide": {**over_of(win)}},
           "old_rule_pick": old_rule, "frozen_val": frozen,
           "variants": sorted(cands, key=lambda c: -np.nan_to_num(c["mean_margin"], nan=-1e9))}
    CHOICE.write_text(json.dumps(_clean(log), indent=1, sort_keys=True), encoding="utf-8")
    for c in (frozen, old_rule, win):
        print(f"== {c['variant']} {c['threshold']} margin {c['mean_margin']:+.2f} "
              f"warm {c['mean_warm']:.2f}", flush=True)
    return log


def over_of(c: dict[str, Any]) -> dict[str, Any]:
    return {**DecideConfig.for_profile("coding_frozen").__dict__, **c["overrides"],
            **c["threshold_cfg"]}


def _pts(c: dict[str, Any]) -> str:
    m = c.get("mean_margin")
    return "outside KA range" if m is None else f"{m:+.2f}"


def _clean(x: Any) -> Any:
    """JSON-safe: NaN -> None, tuples -> lists."""
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_clean(v) for v in x]
    return None if isinstance(x, float) and math.isnan(x) else x


RUNS = [("kind", "kind", 0, "none"), ("aws", "aws", 0, "none"),
        ("aws_cap1_parallel_3", "aws", 1, "parallel_3"),
        ("aws_cap2_parallel_3", "aws", 2, "parallel_3"),
        ("aws_cap3_parallel_3", "aws", 3, "parallel_3"),
        ("aws_uncapped_parallel_3", "aws", 0, "parallel_3")]


def sens(colds: dict[str, ColdStarts]) -> None:
    sim0 = load_config()
    chosen = json.loads(CHOICE.read_text(encoding="utf-8"))["chosen"]["decide"]
    retuned = DecideConfig(**{k: tuple(v) if k == "cost_tau_clamp" else v
                              for k, v in chosen.items()})
    pricing, commit = load_pricing(), git_commit()
    if SENS_CSV.exists():
        SENS_CSV.unlink()
    for src in sim0["sources"]:
        profile = profile_of(src["source"])
        for run, env_name, cap, contention in RUNS:
            cold = colds[env_name]
            sim = {**sim0, "max_concurrent_scale_ups": cap, "contention": contention}
            prep = prepare(src, cold, "test")
            for mode in sim["cold_modes"]:
                env = Env(cold, mode, sim, pricing, DecideConfig.for_profile(profile))
                specs = [s for s in baseline_specs(sim, prep, env)
                         if s.name in KEEP or s.name == "always_warm"]
                frozen = "coding_frozen" if profile == "coding" else profile
                specs.append(ours_spec(f"{profile}_frozen", prep, env,
                                       DecideConfig.for_profile(frozen)))
                if profile == "coding":
                    specs.append(ours_spec("coding_retuned", prep, env, retuned))
                specs += [Spec("oracle", 0.0, oracle=True),
                          Spec("oracle_cap", 0.0, oracle=True, oracle_cap=True)]
                rows = run_specs(prep, specs, env, sim, {"run": run, "profile": profile,
                                                         "scaleup_cap": cap,
                                                         "contention": contention})
                nfr3(rows, next(r for r in rows if r["policy"] == "always_warm"))
                ka = _ka(rows)
                for r in rows:
                    r["git_commit"] = commit
                    mg = ka_margin(float(r["warm_pct"]), float(r["idle_pod_s_per_1k"]), ka)
                    r["margin_pts"] = f"{mg:.3f}"
                    print(f"{run:24s} {prep.source:13s} {mode:8s} {r['policy']:16s} "
                          f"warm {float(r['warm_pct']):6.2f} idle/1k "
                          f"{float(r['idle_pod_s_per_1k']):10.1f} margin {r['margin_pts']}",
                          flush=True)
                _append(SENS_CSV, rows)
    write_md()


def write_md() -> None:
    with _open(SENS_CSV, "r") as f:
        rows = list(csv.DictReader(f))
    get = {(r["run"], r["source"], r["cold_mode"], r["policy"]): r for r in rows}
    srcs = list(dict.fromkeys(r["source"] for r in rows))
    modes = ("cached", "uncached")
    ch = json.loads(CHOICE.read_text(encoding="utf-8"))
    c = ch["chosen"]

    def cell(run: str, s: str, m: str, p: str, what: str = "wi") -> str:
        r = get.get((run, s, m, p))
        if r is None:
            return "-"
        mg = float(r["margin_pts"])
        mm = "outside KA range" if math.isnan(mg) else f"{mg:+.1f}"
        if what == "m":
            return mm
        return f"{float(r['warm_pct']):.1f} / {float(r['idle_pod_s_per_1k']):,.0f}" + \
            ("" if what == "wi" else f" ({mm})")

    def ours(s: str) -> list[str]:
        return ["coding_frozen", "coding_retuned"] if get.get(
            ("kind", s, "cached", "coding_frozen")) else ["support_frozen"]

    out = ["# Coding retune under the scale-up cap, cap sensitivity, final tables", "",
           f"Cold starts: kind (sha256 {rows[0]['cold_start_sha256'] if rows else ''} for the "
           f"first run) and AWS (sha256 {ch['cold_start_sha256']}). git {ch['git_commit']}. "
           "E0 measured 1 and 3 starts at once, NOT 2: in every capped run a start that "
           "overlaps another takes the 3-at-once (parallel_3) time, also when only 2 overlap. "
           "Each session is its own node (per-session pods, no shared pool).", "",
           "## Retune (VAL only, cached, AWS, cap 2, parallel_3)", "",
           f"Rule: {ch['rule']}. Chosen: **{c['variant']} {c['threshold']}** "
           f"(overrides {json.dumps(c['overrides'])}), VAL mean margin "
           f"{c['mean_margin']:+.2f} pts, mean warm {c['mean_warm']:.1f}%. The frozen profile "
           f"on the same VAL: margin {ch['frozen_val']['mean_margin']:+.2f}, warm "
           f"{ch['frozen_val']['mean_warm']:.1f}%. The frozen rule (max warm within the idle "
           f"budget) would pick {ch['old_rule_pick']['variant']} "
           f"{ch['old_rule_pick']['threshold']} (margin {_pts(ch['old_rule_pick'])}"
           f", warm {ch['old_rule_pick']['mean_warm']:.1f}%).", "",
           "Best setting per variant (VAL margin, mean of TraceLab and Copilot):", "",
           "| Variant | best threshold | mean margin | mean warm % | TraceLab warm / idle | "
           "Copilot warm / idle |", "|---|---|---|---|---|---|"]
    best: dict[str, dict[str, Any]] = {}
    for v in ch["variants"]:
        if v["feasible"] and v["mean_margin"] is not None and v["variant"] not in best:
            best[v["variant"]] = v
    for name, v in best.items():
        ps = v["per_source"]
        out.append(f"| {name} | {v['threshold']} | {v['mean_margin']:+.2f} | "
                   f"{v['mean_warm']:.1f} | " + " | ".join(
                       f"{ps[s]['warm_pct']:.1f} / {ps[s]['idle_pod_s_per_1k']:,.0f}"
                       for s in ("tracelab", "copilot_2026")) + " |")
    out += ["", "## Final: frozen vs retuned vs keep-alive 30 s (TEST)", "",
            "Cells: warm % / idle pod-s per 1,000 calls (equal-idle margin vs keep-alive, "
            "points). AWS = no cap, no contention; AWS + cap = cap 2, parallel_3.", "",
            "| Source | Mode | Policy | kind | AWS | AWS + cap |", "|---|---|---|---|---|---|"]
    final = ("kind", "aws", "aws_cap2_parallel_3")
    for s in srcs:
        for m in modes:
            for p in ["keep_alive_30", *ours(s)]:
                out.append(f"| {s} | {m} | {p} | " + " | ".join(
                    cell(r, s, m, p, "wim" if p != "keep_alive_30" else "wi") for r in final)
                    + " |")
    out += ["", "## Sensitivity to the cap (TEST; equal-idle margin vs keep-alive, points)", "",
            "All AWS cold starts with parallel_3 contention; \"uncapped\" = no cap, same "
            "contention. Report only: nothing was tuned on these.", "",
            "| Source | Mode | Policy | cap 1 | cap 2 | cap 3 | uncapped |",
            "|---|---|---|---|---|---|---|"]
    caps = ("aws_cap1_parallel_3", "aws_cap2_parallel_3", "aws_cap3_parallel_3",
            "aws_uncapped_parallel_3")
    for s in srcs:
        for m in modes:
            for p in ours(s):
                out.append(f"| {s} | {m} | {p} | " + " | ".join(
                    cell(r, s, m, p, "m") for r in caps) + " |")
    out += ["", "## Oracle (TEST, warm % / idle pod-s per 1k)", "",
            "`oracle` knows the next batch and starts each pod one cold start before it. With "
            "no cap and no contention (AWS) it is the headroom ceiling. With contention or a "
            "cap it is **not achievable**: its just-in-time starts are slowed or queued. "
            "`oracle_cap` also knows the cap and asks for all of a batch's starts ceil(n / cap) "
            "waves early (a simple plan, not an optimum; at cap 1 no start overlaps another, "
            "so none takes the slower parallel_3 time).", "",
            "| Source | Mode | oracle, AWS no cap, no contention (ceiling) | oracle, "
            "uncapped + parallel_3 (not achievable) | oracle, cap 2 (not achievable) | "
            "oracle_cap, cap 1 | oracle_cap, cap 2 | oracle_cap, cap 3 |",
            "|---|---|---|---|---|---|---|---|"]
    for s in srcs:
        for m in modes:
            out.append(f"| {s} | {m} | {cell('aws', s, m, 'oracle')} | "
                       f"{cell('aws_uncapped_parallel_3', s, m, 'oracle')} | "
                       f"{cell('aws_cap2_parallel_3', s, m, 'oracle')} | " + " | ".join(
                           cell(r, s, m, "oracle_cap") for r in caps[:3]) + " |")
    SENS_MD.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(SENS_MD.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--aws", required=True, help="AWS cold-start file (with parallel_3)")
    p.add_argument("--kind", required=True, help="kind cold-start file")
    p.add_argument("--steps", default="tune,sens")
    args = p.parse_args(argv)
    colds = {k: ColdStarts.load(Path(v).expanduser()) for k, v in
             (("aws", args.aws), ("kind", args.kind))}
    steps = args.steps.split(",")
    if "tune" in steps:
        tune(colds["aws"])
    if "sens" in steps:
        sens(colds)
    if steps == ["md"]:
        write_md()
    return 0


if __name__ == "__main__":
    sys.exit(main())
