"""Val-only tuning of the warm policy (Phase 6, second pass).

    nice -n 10 python -m sim.tune --cold-start PATH

Every number here is on the VAL split (sessions with timing), cached images; the test split
is not read. Each step changes one part of the policy and keeps the others at the previous
step's winner; every variant x threshold is written to results/sim_variants.csv.gz, winners and
losers alike.

Objective (PRD NFR-3), used to pick every winner: over the threshold settings (fixed tau in
``tau_sweep``, cost-based lambda in ``lambda_sweep``), a setting is feasible if on every
source its idle pod-seconds are <= ``nfr3_idle_ratio`` x always-warm's. A variant's score is
the mean warm % over sources of its best feasible setting. Variants with a feasible setting
beat those without; among the latter the one with the smallest worst-source idle ratio wins.

Steps (the order the user set):
  a  copies: quantile 0.9 (old), quantile 0.5 (1 unless the batch more likely needs more),
     always 1. Start rule, keep-after-use: old (timing rule, pod TTL 30 s).
  b  start: spec (gap p50 - cold p95), jit_p50 (gap p50 - cold median), jit_p10 (gap p10 -
     cold median), now (start now, 30 s TTL).
  c  keep-after-use replaces the 30 s keep-alive: 0, 2, 5, 10, 30 s under the contract rule
     (max(latest decision ttl, keep)), and 0, 2, 5, 10 s with the decision TTL holding the
     pod only until its first use ("hold"). Hold variants are run and reported but cannot
     win: the controller implements the P-new max rule, not hold.
  a2, b2  a and b again under the step-c keep rule (with keep 30 s nothing may be feasible,
     and then a and b cannot be told apart). Ties keep the earlier (older) option.
  timing  spec rule vs start-now (and the b2 winner) under the a2/b2/c winners.
  The final threshold is the best feasible setting of the timing-step winner.
Step d (diagnostics: decisions for servers already warm, why prewarmed pods go unused) is
read off the same rows (columns dec_already_warm_pct, unused_*_pct).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from predictors.cold_start import ColdStarts
from predictors.decide import DecideConfig, load_pricing, load_profiles
from predictors.eval import RESULTS, git_commit
from sim.offline import (
    OLD,
    ROOT,
    Env,
    Prepared,
    _append,
    baseline_specs,
    load_config,
    nfr3,
    ours_spec,
    prepare,
    run_specs,
)

VARIANTS_CSV = RESULTS / "sim_variants.csv.gz"
CHOICE_JSON = RESULTS / "sim_choice.json"
MODE = "cached"


def thresholds(sim: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out = [(f"tau{t:.1f}", {"threshold": "fixed", "tau": float(t)}) for t in sim["tau_sweep"]]
    return out + [(f"lam{lam:g}", {"threshold": "cost", "lambda_pod_s": float(lam)})
                  for lam in sim["lambda_sweep"]]


class Tuner:
    def __init__(self, preps: list[Prepared], cold: ColdStarts, sim: dict[str, Any],
                 base: DecideConfig, profile: str = "") -> None:
        self.preps, self.sim, self.base, self.profile = preps, sim, base, profile
        self.env = Env(cold, MODE, sim, load_pricing(), base)
        self.commit = git_commit()
        self.ratio = float(sim["nfr3_idle_ratio"])
        self.aw: dict[str, dict[str, Any]] = {}
        for prep in preps:
            rows = run_specs(prep, baseline_specs(sim, prep, self.env), self.env, sim,
                             {"step": "baselines", "variant": "", "profile": profile})
            self.aw[prep.source] = next(r for r in rows if r["policy"] == "always_warm")
            self._write(rows, prep.source)

    def _write(self, rows: list[dict[str, Any]], source: str) -> None:
        nfr3(rows, self.aw[source])
        for r in rows:
            r["nfr3_feasible"] = float(r["idle_over_always_warm"]) <= self.ratio
            r["git_commit"] = self.commit
            print(f"{r['step']:9s} {r['variant']:22s} {source:13s} {r['policy']:28s} "
                  f"warm {float(r['warm_pct']):6.2f} idle/1k "
                  f"{float(r['idle_pod_s_per_1k']):10.1f} ratio {r['idle_over_always_warm']}",
                  flush=True)
        _append(VARIANTS_CSV, rows)

    def variant(self, step: str, name: str, cfg: dict[str, Any], after_use: str
                ) -> dict[str, Any]:
        """Run one variant over all thresholds and sources; return its score."""
        per: dict[str, dict[str, dict[str, Any]]] = {}
        for prep in self.preps:
            specs = [ours_spec(f"{name}|{tl}", prep, self.env, self.base.with_(**cfg, **tk),
                               after_use) for tl, tk in thresholds(self.sim)]
            rows = run_specs(prep, specs, self.env, self.sim, {"step": step, "variant": name,
                                                             "profile": self.profile})
            self._write(rows, prep.source)
            for (tl, _), r in zip(thresholds(self.sim), rows, strict=True):
                per.setdefault(tl, {})[prep.source] = r
        best: dict[str, Any] | None = None
        best_any = -1.0  # best mean warm over all thresholds, feasible or not
        for tl, tk in thresholds(self.sim):
            rs = per[tl]
            worst = max(float(r["idle_over_always_warm"]) for r in rs.values())
            warm = sum(float(r["warm_pct"]) for r in rs.values()) / len(rs)
            cand = {"variant": name, "cfg": cfg, "after_use": after_use, "threshold": tl,
                    "threshold_cfg": tk, "feasible": worst <= self.ratio, "mean_warm": warm,
                    "worst_idle_ratio": worst,
                    "per_source": {s: {"warm_pct": float(r["warm_pct"]),
                                       "idle_pod_s_per_1k": float(r["idle_pod_s_per_1k"]),
                                       "idle_over_always_warm": float(r["idle_over_always_warm"])}
                                   for s, r in rs.items()}}
            best_any = max(best_any, warm)
            if best is None or _key(cand) > _key(best):
                best = cand
        assert best is not None
        best["best_any_threshold_warm"] = best_any
        best["budget_binding"] = best["feasible"] and best_any > best["mean_warm"] + 1e-9
        print(f"== {step} {name}: {_fmt(best)}", flush=True)
        return best

    def step(self, step: str, options: list[tuple[str, dict[str, Any], str]]
             ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        scores = [self.variant(step, n, c, a) for n, c, a in options]
        for sc in scores:  # the controller implements the P-new max rule (or the 30 s default)
            sc["shippable"] = sc["after_use"] == "contract" or \
                float(sc["cfg"].get("keep_after_use_s", 30.0)) >= 30.0
        win = max((sc for sc in scores if sc["shippable"]), key=_key)
        print(f"==== {step} winner: {_fmt(win)}", flush=True)
        return win, scores


def _key(c: dict[str, Any]) -> tuple[bool, float]:
    return (True, c["mean_warm"]) if c["feasible"] else (False, -c["worst_idle_ratio"])


def _fmt(c: dict[str, Any]) -> str:
    return (f"{c['variant']} {c['threshold']} feasible={c['feasible']} "
            f"mean_warm={c['mean_warm']:.2f} worst_ratio={c['worst_idle_ratio']:.3f} "
            f"binding={c.get('budget_binding')}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--cold-start", default=None)
    p.add_argument("--sources", default="all")
    p.add_argument("--profile", default=None, help="tune one profile on its sources' VAL")
    args = p.parse_args(argv)
    sim = load_config()
    path = Path(args.cold_start or sim["cold_start_path"])
    cold = ColdStarts.load(path if path.is_absolute() else ROOT / path)
    print(f"cold starts: {cold.path} ({cold.label}, sha256 {cold.sha256})", flush=True)
    want = None if args.sources == "all" else set(args.sources.split(","))
    if args.profile is not None:
        want = set(load_profiles()[args.profile]["sources"])
    preps = [prepare(s, cold, "val") for s in sim["sources"]
             if want is None or s["source"] in want]
    base = DecideConfig.load().with_(**OLD)
    t = Tuner(preps, cold, sim, base, args.profile or "")
    log: dict[str, Any] = {"split": "val", "cold_mode": MODE, "cold_start": cold.label,
                           "cold_start_sha256": cold.sha256, "git_commit": t.commit,
                           "objective": f"max mean warm % s.t. idle <= {t.ratio} x always-warm "
                                        "on every source", "steps": {}}

    copies = {"q0.9": {"copies_quantile": 0.9}, "q0.5": {"copies_quantile": 0.5},
              "one": {"max_copies_per_tool": 1}}
    win, sc = t.step("a_copies", [(f"copies={k}", v, "pod") for k, v in copies.items()])
    log["steps"]["a_copies"] = {"winner": win, "all": sc}
    cfg = dict(win["cfg"])

    starts = {"spec": {"start_rule": "timing", "gap_stat": "p50", "plan_stat": "p95_ms"},
              "jit_p50": {"start_rule": "timing", "gap_stat": "p50", "plan_stat": "median_ms"},
              "jit_p10": {"start_rule": "timing", "gap_stat": "p10", "plan_stat": "median_ms"},
              "now": {"start_rule": "now"}}
    win, sc = t.step("b_start", [(f"start={k}", {**cfg, **v}, "pod") for k, v in starts.items()])
    log["steps"]["b_start"] = {"winner": win, "all": sc}
    b_win = win["variant"].split("=", 1)[1]
    cfg = dict(win["cfg"])

    opts = [(f"keep={k}s,contract", {**cfg, "keep_after_use_s": float(k)}, "contract")
            for k in (0, 2, 5, 10, 30)]
    opts += [(f"keep={k}s,hold", {**cfg, "keep_after_use_s": float(k)}, "pod")
             for k in (0, 2, 5, 10)]
    win, sc = t.step("c_keep", opts)
    log["steps"]["c_keep"] = {"winner": win, "all": sc}
    cfg, after_use = dict(win["cfg"]), win["after_use"]

    # round 2: a and b again under the step-c keep rule (with keep 30 s nothing may be
    # feasible, and then a and b cannot be told apart)
    win, sc = t.step("a2_copies", [(f"copies={k}", {**cfg, **v}, after_use)
                                   for k, v in copies.items()])
    log["steps"]["a2_copies"] = {"winner": win, "all": sc}
    cfg = dict(win["cfg"])
    win, sc = t.step("b2_start", [(f"start={k}", {**cfg, **v}, after_use)
                                  for k, v in starts.items()])
    log["steps"]["b2_start"] = {"winner": win, "all": sc}
    b_win = win["variant"].split("=", 1)[1]
    cfg = dict(win["cfg"])
    # item 2: the spec rule vs start now (plus the round-2 winner if it is neither)
    rules = ["spec", "now"] + ([b_win] if b_win not in ("spec", "now") else [])
    win, sc = t.step("timing", [(f"start={k}", {**cfg, **starts[k]}, after_use) for k in rules])
    log["steps"]["timing"] = {"winner": win, "all": sc}
    log["chosen"] = {"decide": {**win["cfg"], **win["threshold_cfg"]},
                     "after_use": win["after_use"], "threshold": win["threshold"],
                     "feasible": win["feasible"], "mean_warm": win["mean_warm"]}
    log["chosen"]["budget_binding"] = win["budget_binding"]
    log["chosen"]["best_any_threshold_warm"] = win["best_any_threshold_warm"]
    log["profile"], log["sources"] = args.profile, [pr.source for pr in preps]
    out = CHOICE_JSON if args.profile is None else RESULTS / f"sim_choice_{args.profile}.json"
    out.write_text(json.dumps(log, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps(log["chosen"], indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
