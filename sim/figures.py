"""Figures and markdown summary from results/sim_offline.csv.gz (Phase 6).

    python -m sim.figures

- results/sim_pareto.png: per source x cold mode, idle pod-seconds per 1,000 calls (log x)
  vs % calls cold; our old policy (tau sweep) and the shipped one (lambda and tau sweeps) as
  lines, its default as a marker, baselines as labelled points.
- results/tau_curve.png: per source (cached images), % calls warm on arrival and % of
  prewarmed pods used, vs tau, old vs shipped policy.
- results/sim_report.md: the tables, and whether any of our points beats the keep-alive
  curve (warm % above keep-alive's at the same idle, log-linear between keep-alive points).
- results/sim_added_cdf.png: per source x cold mode, CDF of the latency a call waits for a
  pod (0 when warm), for keep-alive 30 s, Markov-1, our profile policy and the oracle; the
  primary run solid, kind dashed.
- results/sim_kind_vs_aws.md: the same policies under every run (cold-start environment x
  scale-up cap x contention), and the equal-idle margins.
Runs are told apart by cold-start environment, scale-up cap and contention. The primary run
(the newest rows) writes the names above; kind writes ``*_kind`` copies of the first three.
Every figure carries the cold-start file's label (environment and status).
"""

from __future__ import annotations

import json
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from sim.offline import CSV_PATH, HIDE_CSV, RESULTS  # noqa: E402

SERIES = {"old": ("#2a78d6", "-", "ours, old (τ sweep)"),
          "new_lam": ("#eb6834", "-", "ours, shipped (λ sweep)"),
          "new_tau": ("#eb6834", "--", "ours, shipped (τ sweep)")}
OURS_COLOR = "#eb6834"
GRAY, INK, MUTED, GRID = "#6f6e69", "#0b0b0b", "#52514e", "#e6e5e0"
REF = {"scale_to_zero": ("s", 70, "scale-to-zero"), "always_warm": ("^", 80, "always-warm"),
       "markov1_top1": ("D", 55, "Markov-1 top-1 (τ 0.6)"),
       "oracle": ("*", 180, "oracle")}
KEEP = ["keep_alive_5", "keep_alive_10", "keep_alive_30", "keep_alive_60", "keep_alive_300",
        "keep_alive_600"]


def run_tag(label: str, cap: object = "", contention: object = "") -> str:
    """``kind``, ``aws`` (no scale-up cap, no contention) or e.g. ``aws_cap2_parallel_3``."""
    env = str(label).split(":")[0].strip() or "unknown"
    cap, contention = str(cap or "0"), str(contention or "none")
    return env if cap == "0" and contention == "none" else f"{env}_cap{cap}_{contention}"


def latest() -> pd.DataFrame:
    df = pd.read_csv(CSV_PATH, dtype={"vocab": str}, keep_default_na=False)
    if "split" in df:
        df = df[df.split.isin(["test", ""])]
    for c in ("scaleup_cap", "contention"):
        if c not in df:
            df[c] = ""
    df["run"] = [run_tag(*v) for v in zip(df.cold_start_label, df.scaleup_cap, df.contention,
                                          strict=True)]
    df = df.sort_values("time").groupby(["run", "source", "cold_mode", "policy"],
                                        as_index=False).last()
    df["lambda_pod_s"] = df.policy.str.extract(r"_lam([0-9.]+)$")[0].astype(float)
    for c in ("warm_pct", "cold_pct", "idle_pod_s_per_1k", "prewarm_used_pct", "tau",
              "cost_usd_per_1k", "cold_pct_hideable", "cold_pct_nonhideable", "hideable_pct",
              "batch_full_warm_pct", "added_ms_mean", "fr2_call_ready_pct",
              "fr2_sessions_all_pct", "fr2_sessions_80_pct", "nfr3_unused_over_always_warm",
              "idle_over_always_warm", "unused_wrong_server_pct", "unused_surplus_copy_pct",
              "unused_expired_pct", "dec_already_warm_pct", "scaleup_queued_pct",
              "scaleup_contended_pct", "scaleup_queue_ms_mean"):
        df[c] = pd.to_numeric(df[c], errors="coerce") if c in df else np.nan
    return df


def _family(policy: str) -> str | None:
    for pre, fam in (("ours_old_tau", "old"), ("ours_new_tau", "new_tau"),
                     ("ours_new_lam", "new_lam")):
        if policy.startswith(pre):
            return fam
    return None


def _style(ax: plt.Axes) -> None:
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=8)


def pareto(df: pd.DataFrame, label: str, suffix: str = "") -> None:
    srcs = list(dict.fromkeys(df.source))
    modes = ["cached", "uncached"]
    fig, axes = plt.subplots(len(modes), len(srcs), figsize=(5.2 * len(srcs), 4.4 * len(modes)),
                             squeeze=False)
    for i, mode in enumerate(modes):
        for j, src in enumerate(srcs):
            ax, d = axes[i][j], df[(df.source == src) & (df.cold_mode == mode)]
            _style(ax)
            for fam, (color, ls, name) in SERIES.items():
                f = d[d.policy.map(_family) == fam].sort_values("idle_pod_s_per_1k")
                ax.plot(f.idle_pod_s_per_1k.clip(lower=1), f.cold_pct, ls, color=color,
                        linewidth=2, marker="o", markersize=5, label=name, zorder=3)
            sh = d[d.policy == "ours_new"]
            if len(sh):
                ax.scatter([max(float(sh.idle_pod_s_per_1k.iloc[0]), 1.0)],
                           [float(sh.cold_pct.iloc[0])], marker="P", s=130, color=OURS_COLOR,
                           edgecolor="white", linewidth=1.5, zorder=5,
                           label="ours, shipped default")
            k = d.set_index("policy").reindex(KEEP).dropna(subset=["cold_pct"])
            ax.plot(k.idle_pod_s_per_1k, k.cold_pct, ":", color=GRAY, linewidth=1.5,
                    marker="o", markersize=6, label="keep-alive 5/10/30/60/300/600 s", zorder=2)
            for pol, txt in (("keep_alive_5", "5 s"), ("keep_alive_30", "30 s"),
                             ("keep_alive_600", "600 s")):
                if pol in k.index:
                    ax.annotate(txt, (k.loc[pol].idle_pod_s_per_1k, k.loc[pol].cold_pct),
                                textcoords="offset points", xytext=(-6, -12), fontsize=7,
                                color=INK)
            for pol, (mk, size, name) in REF.items():
                r = d[d.policy == pol]
                if r.empty:
                    continue
                x, y = max(float(r.idle_pod_s_per_1k.iloc[0]), 1.0), float(r.cold_pct.iloc[0])
                ax.scatter([x], [y], marker=mk, s=size, color=GRAY, edgecolor="white",
                           linewidth=1.5, zorder=4, label=name)
            ax.set_xscale("log")
            ax.set_ylim(-3, 103)
            ax.set_title(f"{src} · {mode} images", fontsize=10, color=INK, loc="left")
            if i == len(modes) - 1:
                ax.set_xlabel("idle pod-seconds per 1,000 calls (log)", fontsize=8, color=MUTED)
            if j == 0:
                ax.set_ylabel("% calls cold on arrival", fontsize=8, color=MUTED)
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=4, fontsize=8, frameon=False)
    fig.suptitle(f"Warm-pool Pareto (offline replay of test sessions)\ncold starts: {label}",
                 fontsize=11, color=INK, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.savefig(RESULTS / f"sim_pareto{suffix}.png", dpi=130)
    plt.close(fig)


def tau_curve(df: pd.DataFrame, label: str, suffix: str = "") -> None:
    srcs = list(dict.fromkeys(df.source))
    fig, axes = plt.subplots(1, len(srcs), figsize=(5.0 * len(srcs), 4.3), squeeze=False)
    metrics = [("warm_pct", "#2a78d6", "% calls warm on arrival"),
               ("prewarm_used_pct", "#eb6834", "% prewarmed pods used")]
    for j, src in enumerate(srcs):
        ax, d = axes[0][j], df[(df.source == src) & (df.cold_mode == "cached")]
        _style(ax)
        for fam, ls, fname in (("old", "-", "old"), ("new_tau", "--", "shipped")):
            f = d[d.policy.map(_family) == fam].sort_values("tau")
            for col, color, mname in metrics:
                ax.plot(f.tau, f[col], ls, color=color, linewidth=2, marker="o", markersize=4,
                        label=f"{mname} ({fname})")
        ax.axvline(0.6, color=MUTED, linewidth=0.8, linestyle=":")
        ax.set_ylim(0, 100)
        ax.set_title(f"{src} · cached images", fontsize=10, color=INK, loc="left")
        ax.set_xlabel("threshold τ", fontsize=8, color=MUTED)
        if j == 0:
            ax.set_ylabel("%", fontsize=8, color=MUTED)
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=4, fontsize=8, frameon=False)
    fig.suptitle(f"Threshold curve: coverage vs precision of prewarming\ncold starts: {label}",
                 fontsize=11, color=INK, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0.1, 1, 1))
    fig.savefig(RESULTS / f"tau_curve{suffix}.png", dpi=130)
    plt.close(fig)


def beats_keep_alive(d: pd.DataFrame) -> pd.DataFrame:
    """Our points vs the keep-alive curve of one source x mode: keep-alive warm % at the
    same idle (linear in log idle between keep-alive points; only inside their range)."""
    k = d.set_index("policy").reindex(KEEP).dropna(subset=["warm_pct"]).sort_values(
        "idle_pod_s_per_1k")
    lx = np.log(k.idle_pod_s_per_1k.clip(lower=1).to_numpy())
    ours = d[d.policy.map(_family).notna() | (d.policy == "ours_new")].copy()
    x = np.log(ours.idle_pod_s_per_1k.clip(lower=1).to_numpy())
    inside = (x >= lx.min()) & (x <= lx.max())
    ours["keep_alive_warm_at_idle"] = np.where(inside, np.interp(x, lx, k.warm_pct), np.nan)
    ours["margin_pts"] = ours.warm_pct - ours.keep_alive_warm_at_idle
    return ours


def report(df: pd.DataFrame, label: str) -> str:
    keep = ["scale_to_zero", "always_warm", *KEEP, "markov1_top1", "ours_old_tau0.3",
            "ours_old_tau0.6", "ours_old_tau0.9", "ours_old_now_tau0.6", "ours_new_tau0.3",
            "ours_new_tau0.6", "ours_new_tau0.9", "ours_new_lam1", "ours_new_lam3",
            "ours_new_lam10", "ours_new_lam30", "ours_new_lam100", "ours_new", "oracle"]
    out = ["# Offline simulation (Phase 6)", "", f"Cold starts: **{label}**. Pricing: "
           "assumptions (configs/model/pricing.yaml). Definitions: sim/offline.py docstring. "
           "Test split. `ours_old_*`: the first-pass policy (copies q0.9, timing rule, 30 s "
           "after use); `ours_new*`: the policy val picked (configs/model/decide.yaml; "
           "`ours_new` is its default). Variants tried on val: results/sim_variants.csv.gz.", ""]
    beat_lines = ["## Does any of our points beat keep-alive? (test)", "",
                  "Margin = our warm % minus keep-alive's at the same idle pod-seconds "
                  "(log-linear between keep-alive 5 s ... 600 s; points outside that range are "
                  "not compared).", "",
                  "| Source | Mode | our points compared | above keep-alive | best margin (pts) "
                  "| at | shipped default margin |", "|---|---|---|---|---|---|---|"]
    for (src, mode), d in df.groupby(["source", "cold_mode"], sort=False):
        bk = beats_keep_alive(d)
        cmp_ = bk.dropna(subset=["margin_pts"])
        top = cmp_.loc[cmp_.margin_pts.idxmax()] if len(cmp_) else None
        dflt = bk[bk.policy == "ours_new"].margin_pts
        beat_lines.append(
            f"| {src} | {mode} | {len(cmp_)} | {(cmp_.margin_pts > 0).sum()} | "
            f"{'-' if top is None else format(top.margin_pts, '+.1f')} | "
            f"{'-' if top is None else top.policy} | "
            f"{'-' if dflt.empty or pd.isna(dflt.iloc[0]) else format(dflt.iloc[0], '+.1f')} |")
        d = d.set_index("policy")
        r0 = d.iloc[0]
        out += [f"## {src} · {mode} images ({int(r0.sessions)} test sessions, "
                f"{int(r0.calls)} calls; hideable {r0.hideable_pct:.1f}%)", "",
                "| Policy | warm % | batches fully warm % | cold % hideable / non-hideable | "
                "added ms/call | idle pod-s /1k | idle / always-warm | $ /1k calls | "
                "prewarm used % | unused: wrong server / surplus copy / expired % | "
                "decisions already warm % | FR-2 call / session-all / session-80 |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for p in keep:
            if p not in d.index:
                continue
            r = d.loc[p]

            def f(v: float, fmt: str = ".1f") -> str:
                return "-" if pd.isna(v) else format(v, fmt)

            fr2 = (f"{f(r.fr2_call_ready_pct)} / {f(r.fr2_sessions_all_pct)} / "
                   f"{f(r.fr2_sessions_80_pct)}" if not pd.isna(r.fr2_call_ready_pct) else "-")
            unused = (f"{f(r.unused_wrong_server_pct)} / {f(r.unused_surplus_copy_pct)} / "
                      f"{f(r.unused_expired_pct)}" if not pd.isna(r.unused_wrong_server_pct)
                      else "-")
            out.append(f"| {p} | {f(r.warm_pct)} | {f(r.batch_full_warm_pct)} | "
                       f"{f(r.cold_pct_hideable)} / {f(r.cold_pct_nonhideable)} | "
                       f"{f(r.added_ms_mean, '.0f')} | {f(r.idle_pod_s_per_1k, '.0f')} | "
                       f"{f(r.idle_over_always_warm, '.3f')} | "
                       f"{f(r.cost_usd_per_1k, '.4f')} | {f(r.prewarm_used_pct)} | {unused} | "
                       f"{f(r.dec_already_warm_pct)} | {fr2} |")
        out.append("")
    out += beat_lines + [""]
    h = pd.read_csv(HIDE_CSV, dtype={"vocab": str}, keep_default_na=False)
    h = h[h.cold_start_label == label.split(" · run ")[0]]  # depends on the file only
    h = h.sort_values("time").groupby(["source", "cold_mode", "tool_class"],
                                      as_index=False).last()
    h["hideable_pct"] = pd.to_numeric(h.hideable_pct)
    piv = h.pivot_table(index=["source", "tool_class"], columns="cold_mode",
                        values="hideable_pct").reset_index()
    calls = h[h.cold_mode == "cached"].set_index(["source", "tool_class"]).calls
    out += ["## Hideable fraction (think gap >= the server's median cold start)", "",
            "| Source | Tool class | calls | cached % | uncached % |", "|---|---|---|---|---|"]
    for _, r in piv.iterrows():
        out.append(f"| {r.source} | {r.tool_class} | {int(calls[(r.source, r.tool_class)])} | "
                   f"{r.cached:.1f} | {r.uncached:.1f} |")
    return "\n".join(out) + "\n"


CDF_POLICIES = {"keep_alive_30": (GRAY, "keep-alive 30 s"),
                "markov1_top1": ("#2a78d6", "Markov-1 top-1"),
                "ours_new": (OURS_COLOR, "ours (profile)"), "oracle": (INK, "oracle")}


def added_cdf(df: pd.DataFrame, primary: str) -> None:
    """CDF of the wait per call; primary run solid, kind dashed (rows with the CDF column)."""
    d0 = df[df.get("added_ms_cdf", pd.Series("", index=df.index)).astype(str) != ""]
    srcs = list(dict.fromkeys(d0.source))
    modes = ["cached", "uncached"]
    runs = [(primary, "-")] + ([("kind", "--")] if "kind" in set(d0.run) else [])
    fig, axes = plt.subplots(len(modes), len(srcs), figsize=(5.2 * len(srcs), 4.0 * len(modes)),
                             squeeze=False)
    levels = np.arange(0, 101, 2)
    for i, mode in enumerate(modes):
        for j, src in enumerate(srcs):
            ax = axes[i][j]
            _style(ax)
            for run, ls in runs:
                for pol, (color, name) in CDF_POLICIES.items():
                    r = d0[(d0.run == run) & (d0.source == src) & (d0.cold_mode == mode)
                           & (d0.policy == pol)]
                    if r.empty:
                        continue
                    q = np.asarray(json.loads(r.added_ms_cdf.iloc[0])) / 1000.0
                    ax.step(q, levels, where="post", linestyle=ls, color=color, linewidth=2,
                            label=f"{name} · {run}")
            ax.set_ylim(0, 101)
            ax.set_xlim(left=-0.2)
            ax.set_title(f"{src} · {mode} images", fontsize=10, color=INK, loc="left")
            if i == len(modes) - 1:
                ax.set_xlabel("wait for a pod per call (s)", fontsize=8, color=MUTED)
            if j == 0:
                ax.set_ylabel("% of calls waiting at most x", fontsize=8, color=MUTED)
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=4, fontsize=8, frameon=False)
    fig.suptitle("Cold-start wait per call (test sessions); solid: "
                 f"{primary}, dashed: kind", fontsize=11, color=INK, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    fig.savefig(RESULTS / "sim_added_cdf.png", dpi=130)
    plt.close(fig)


def compare(df: pd.DataFrame, primary: str) -> str:
    """Key policies and equal-idle margins under every run, side by side."""
    runs = ["kind"] + sorted(r for r in set(df.run) if r not in ("kind", primary)) + [primary]
    runs = [r for r in runs if r in set(df.run)]
    pols = ["keep_alive_30", "keep_alive_60", "markov1_top1", "ours_old_tau0.6", "ours_new",
            "oracle"]
    out = ["# Simulator: kind vs AWS cold starts (test split)", "",
           "Runs: " + ", ".join(f"`{r}`" for r in runs) + f". Primary: `{primary}` (AWS "
           "cold starts, at most 2 scale-ups at once per node, a start that overlaps another "
           "takes the parallel_3 time). The policy is frozen: no re-tuning on AWS values. "
           "Cells: warm % / idle pod-s per 1,000 calls.", ""]
    hdr = "| Source | Mode | Policy | " + " | ".join(runs) + " |"
    out += [hdr, "|" + "---|" * (3 + len(runs))]
    for (src, mode), d in df.groupby(["source", "cold_mode"], sort=False):
        for p in pols:
            cells = []
            for r in runs:
                x = d[(d.run == r) & (d.policy == p)]
                cells.append("-" if x.empty else
                             f"{x.warm_pct.iloc[0]:.1f} / {x.idle_pod_s_per_1k.iloc[0]:,.0f}")
            out.append(f"| {src} | {mode} | {p} | " + " | ".join(cells) + " |")
    out += ["", "## Equal-idle margin of our profile policy vs keep-alive (points)", "",
            "| Source | Mode | " + " | ".join(f"{r}: profile / best / above" for r in runs)
            + " |", "|" + "---|" * (2 + len(runs))]
    for (src, mode), d in df.groupby(["source", "cold_mode"], sort=False):
        cells = []
        for r in runs:
            bk = beats_keep_alive(d[d.run == r])
            c = bk.dropna(subset=["margin_pts"])
            m = bk[bk.policy == "ours_new"].margin_pts
            mm = "-" if m.empty or pd.isna(m.iloc[0]) else f"{m.iloc[0]:+.1f}"
            best = f"{c.margin_pts.max():+.1f}" if len(c) else "-"
            cells.append(f"{mm} / {best} / {(c.margin_pts > 0).sum()} of {len(c)}")
        out.append(f"| {src} | {mode} | " + " | ".join(cells) + " |")
    p = df[df.run == primary]
    out += ["", f"## Scale-up queue and contention (`{primary}`)", "",
            "| Source | Mode | Policy | queued % of starts | mean queue wait ms | "
            "overlapping starts % |", "|---|---|---|---|---|---|"]
    for (src, mode), d in p.groupby(["source", "cold_mode"], sort=False):
        for pol in ("keep_alive_30", "markov1_top1", "ours_new", "oracle"):
            x = d[d.policy == pol]
            if x.empty:
                continue
            x = x.iloc[0]
            out.append(f"| {src} | {mode} | {pol} | {x.scaleup_queued_pct:.1f} | "
                       f"{x.scaleup_queue_ms_mean:.0f} | {x.scaleup_contended_pct:.1f} |")
    return "\n".join(out) + "\n"


def main() -> int:
    df = latest()
    primary = str(df.sort_values("time").run.iloc[-1])
    for run, suffix in ((primary, ""), ("kind", "_kind")):
        d = df[df.run == run]
        if d.empty or (run == "kind" and primary == "kind" and suffix):
            continue
        label = str(d.cold_start_label.iloc[-1])
        if run != run_tag(label):  # say which scale-up model the run used
            label += f" · run {run}"
        pareto(d, label, suffix)
        tau_curve(d, label, suffix)
        (RESULTS / f"sim_report{suffix}.md").write_text(report(d, label), encoding="utf-8")
    added_cdf(df, primary)
    (RESULTS / "sim_kind_vs_aws.md").write_text(compare(df, primary), encoding="utf-8")
    print(f"primary run {primary}: wrote results/sim_pareto*.png, tau_curve*.png, "
          "sim_report*.md, sim_added_cdf.png, sim_kind_vs_aws.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
