"""Where prediction can pay off: the cold calls left under keep-alive 30 s, by cause.

    nice -n 10 python -m sim.cold_decomp --cold-start PATH     # test split, cached images

Analysis only (nothing is tuned on it). Every test session with timing is replayed under
keep_alive_30 and under the source's shipped profile (``ours_new``, configs/model/
profiles.yaml), call by call. Each call that is cold under keep-alive 30 s gets one cause:

- ``first_use``: no earlier call in the session went to its server (pods are per server, so
  this is the first use of the server, not of the tool class);
- ``gap_over_30s``: there was an earlier call to the server, but the latest one ended more
  than 30 s before this arrival, so every pod had expired;
- ``other``: a pod existed but was busy (parallel calls to one server, overlapping calls).

For each cause: its share of all calls, and the share of those calls our policy warms. The
idle pod-seconds per 1,000 calls of both policies are reported per source (idle cannot be
split by cause). Writes results/sim_cold_decomp.csv and .md.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from predictors.cold_start import ColdStarts
from predictors.decide import DecideConfig, load_pricing, profile_of
from predictors.eval import RESULTS, git_commit
from sim.offline import (
    ROOT,
    Env,
    Spec,
    Stats,
    _append,
    batch_times,
    load_config,
    ours_spec,
    prepare,
    simulate,
)

CAUSES = ("first_use", "gap_over_30s", "other")
CSV = RESULTS / "sim_cold_decomp.csv"


def causes(ep, env: Env, keep_ms: float) -> list[str]:  # noqa: ANN001
    """Cause per call (event order) if it were cold under keep-alive; '' if not classified."""
    times = batch_times(ep)
    last_end: dict[str, float] = {}
    out = []
    for b, (lo, hi) in enumerate(ep.batches):
        t = times[b][0]
        ends = []
        for e in ep.events[lo:hi]:
            s = env.cold.server(e.tool)
            if s not in last_end:
                out.append("first_use")
            elif last_end[s] + keep_ms < t:
                out.append("gap_over_30s")
            else:
                out.append("other")
            ends.append((s, t + float(e.service_ms or 0.0)))
        for s, end in ends:
            last_end[s] = max(last_end.get(s, 0.0), end)
    return out


def run(src: dict, sim: dict, cold: ColdStarts) -> list[dict]:
    prep = prepare(src, cold, "test")
    prof = profile_of(prep.source)
    dcfg = DecideConfig.for_profile(prof) if prof else DecideConfig.load()
    env = Env(cold, "cached", sim, load_pricing(), dcfg)
    ka = Spec("keep_alive_30", 30_000.0)
    ours = ours_spec("ours_new", prep, env, dcfg)
    n: Counter[str] = Counter()
    cold_ka: Counter[str] = Counter()
    warm_ours: Counter[str] = Counter()
    sk, so = Stats(), Stats()
    for ep in prep.eps:
        a, b = Stats(calls_warm=[]), Stats(calls_warm=[])
        simulate(ep, ka, env, prep.servers, a)
        simulate(ep, ours, env, prep.servers, b)
        for st, tot in ((a, sk), (b, so)):
            tot.calls += st.calls
            tot.idle_ms += st.idle_ms
        assert a.calls_warm is not None and b.calls_warm is not None
        for c, wk, wo in zip(causes(ep, env, 30_000.0), a.calls_warm, b.calls_warm,
                             strict=True):
            n["all"] += 1
            if not wk:
                cold_ka[c] += 1
                warm_ours[c] += wo
    now, commit = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), git_commit()
    rows = []
    for c in CAUSES:
        rows.append({"time": now, "git_commit": commit, "split": "test", "cold_mode": "cached",
                     "source": prep.source, "profile": prof or "", "cause": c,
                     "calls": n["all"], "cold_under_ka30": cold_ka[c],
                     "pct_of_calls": f"{100.0 * cold_ka[c] / n['all']:.3f}",
                     "ours_warms_pct": (f"{100.0 * warm_ours[c] / cold_ka[c]:.3f}"
                                        if cold_ka[c] else ""),
                     "ka30_idle_per_1k": f"{sk.idle_ms / sk.calls:.1f}",
                     "ours_idle_per_1k": f"{so.idle_ms / so.calls:.1f}",
                     "cold_start_label": cold.label, "cold_start_sha256": cold.sha256,
                     "scaleup_cap": env.cap, "contention": env.contention})
    return rows


def report(rows: list[dict]) -> str:
    r0 = rows[0]
    out = ["# Cold calls left under keep-alive 30 s, by cause (test, cached images)", "",
           f"Cold starts: {r0['cold_start_label']}; scale-up cap {r0['scaleup_cap']}, "
           f"contention {r0['contention']}.", "",
           "| Source | Profile | cause | % of all calls (cold under KA30) | share ours warms | "
           "idle /1k: KA30 -> ours |", "|---|---|---|---|---|---|"]
    for r in rows:
        w = r["ours_warms_pct"]
        out.append(f"| {r['source']} | {r['profile']} | {r['cause']} | "
                   f"{float(r['pct_of_calls']):.1f} | {'-' if not w else format(float(w), '.1f')}"
                   f" | {float(r['ka30_idle_per_1k']):,.0f} -> "
                   f"{float(r['ours_idle_per_1k']):,.0f} |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--cold-start", default=None)
    a = p.parse_args(argv)
    sim = load_config()
    path = Path(a.cold_start or sim["cold_start_path"])
    cold = ColdStarts.load(path if path.is_absolute() else ROOT / path)
    rows = [r for s in sim["sources"] for r in run(s, sim, cold)]
    _append(CSV, rows)
    (RESULTS / "sim_cold_decomp.md").write_text(report(rows), encoding="utf-8")
    print(report(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
