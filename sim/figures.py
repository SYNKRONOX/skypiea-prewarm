"""Figures and markdown summary from results/sim_offline.csv (Phase 6).

    python -m sim.figures

- results/sim_pareto.png: per source x cold mode, idle pod-seconds per 1,000 calls (log x)
  vs % calls cold; our two policy families as lines over tau, baselines as labelled points.
- results/tau_curve.png: per source (cached images), % calls warm on arrival and % of
  prewarmed pods used, vs tau, for both families.
- results/sim_report.md: the tables.
Every figure carries the cold-start file's label (environment and status).
"""

from __future__ import annotations

import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from sim.offline import CSV_PATH, HIDE_CSV, RESULTS  # noqa: E402

SERIES = {"ours": ("#2a78d6", "-", "ours: timing rule"),
          "ours_now": ("#eb6834", "--", "ours: start now, 30 s TTL")}
GRAY, INK, MUTED, GRID = "#6f6e69", "#0b0b0b", "#52514e", "#e6e5e0"
REF = {"scale_to_zero": ("s", 70, "scale-to-zero"), "always_warm": ("^", 80, "always-warm"),
       "markov1_top1": ("D", 55, "Markov-1 top-1 (τ 0.6)"),
       "ours_cost": ("P", 90, "ours, cost-based τ"), "oracle": ("*", 180, "oracle")}
KEEP = ["keep_alive_30", "keep_alive_60", "keep_alive_300", "keep_alive_600"]


def latest() -> pd.DataFrame:
    df = pd.read_csv(CSV_PATH, dtype={"vocab": str}, keep_default_na=False)
    df = df.sort_values("time").groupby(["source", "cold_mode", "policy"], as_index=False).last()
    for c in ("warm_pct", "cold_pct", "idle_pod_s_per_1k", "prewarm_used_pct", "tau",
              "cost_usd_per_1k", "cold_pct_hideable", "cold_pct_nonhideable", "hideable_pct",
              "batch_full_warm_pct", "added_ms_mean", "fr2_call_ready_pct",
              "fr2_sessions_all_pct", "fr2_sessions_80_pct", "nfr3_unused_over_always_warm"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _family(policy: str) -> str | None:
    if policy.startswith("ours_now_tau"):
        return "ours_now"
    if policy.startswith("ours_tau"):
        return "ours"
    return None


def _style(ax: plt.Axes) -> None:
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=8)


def pareto(df: pd.DataFrame, label: str) -> None:
    srcs = list(dict.fromkeys(df.source))
    modes = ["cached", "uncached"]
    fig, axes = plt.subplots(len(modes), len(srcs), figsize=(5.2 * len(srcs), 4.4 * len(modes)),
                             squeeze=False)
    for i, mode in enumerate(modes):
        for j, src in enumerate(srcs):
            ax, d = axes[i][j], df[(df.source == src) & (df.cold_mode == mode)]
            _style(ax)
            for fam, (color, ls, name) in SERIES.items():
                f = d[d.policy.map(_family) == fam].sort_values("tau")
                ax.plot(f.idle_pod_s_per_1k.clip(lower=1), f.cold_pct, ls, color=color,
                        linewidth=2, marker="o", markersize=5, label=name, zorder=3)
                if len(f):
                    lo = f.iloc[0]
                    ax.annotate(f"τ {lo.tau:.1f}", (max(lo.idle_pod_s_per_1k, 1), lo.cold_pct),
                                textcoords="offset points", xytext=(4, -10), fontsize=7,
                                color=MUTED)
            k = d.set_index("policy").reindex(KEEP).dropna(subset=["cold_pct"])
            ax.plot(k.idle_pod_s_per_1k, k.cold_pct, ":", color=GRAY, linewidth=1.5,
                    marker="o", markersize=6, label="keep-alive 30/60/300/600 s", zorder=2)
            for pol, txt in (("keep_alive_30", "30 s"), ("keep_alive_600", "600 s")):
                if pol in k.index:
                    ax.annotate(txt, (k.loc[pol].idle_pod_s_per_1k, k.loc[pol].cold_pct),
                                textcoords="offset points", xytext=(5, 5), fontsize=7,
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
    fig.savefig(RESULTS / "sim_pareto.png", dpi=130)
    plt.close(fig)


def tau_curve(df: pd.DataFrame, label: str) -> None:
    srcs = list(dict.fromkeys(df.source))
    fig, axes = plt.subplots(1, len(srcs), figsize=(5.0 * len(srcs), 4.3), squeeze=False)
    metrics = [("warm_pct", "#2a78d6", "% calls warm on arrival"),
               ("prewarm_used_pct", "#eb6834", "% prewarmed pods used")]
    for j, src in enumerate(srcs):
        ax, d = axes[0][j], df[(df.source == src) & (df.cold_mode == "cached")]
        _style(ax)
        for fam, ls, fname in (("ours", "-", "timing rule"), ("ours_now", "--", "start now")):
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
    fig.savefig(RESULTS / "tau_curve.png", dpi=130)
    plt.close(fig)


def report(df: pd.DataFrame, label: str) -> str:
    keep = ["scale_to_zero", "always_warm", "keep_alive_30", "keep_alive_60", "keep_alive_300",
            "keep_alive_600", "markov1_top1", "ours_tau0.3", "ours_tau0.6", "ours_tau0.9",
            "ours_now_tau0.3", "ours_now_tau0.6", "ours_now_tau0.9", "ours_cost", "oracle"]
    out = ["# Offline simulation (Phase 6)", "", f"Cold starts: **{label}**. Pricing: "
           "assumptions (configs/model/pricing.yaml). Definitions: sim/offline.py docstring.",
           ""]
    for (src, mode), d in df.groupby(["source", "cold_mode"], sort=False):
        d = d.set_index("policy")
        r0 = d.iloc[0]
        out += [f"## {src} · {mode} images ({int(r0.sessions)} test sessions, "
                f"{int(r0.calls)} calls; hideable {r0.hideable_pct:.1f}%)", "",
                "| Policy | warm % | batches fully warm % | cold % hideable / non-hideable | "
                "added ms/call | idle pod-s /1k | $ /1k calls | prewarm used % | "
                "NFR-3 unused/always-warm | FR-2 call / session-all / session-80 |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        for p in keep:
            if p not in d.index:
                continue
            r = d.loc[p]

            def f(v: float, fmt: str = ".1f") -> str:
                return "-" if pd.isna(v) else format(v, fmt)

            fr2 = (f"{f(r.fr2_call_ready_pct)} / {f(r.fr2_sessions_all_pct)} / "
                   f"{f(r.fr2_sessions_80_pct)}" if not pd.isna(r.fr2_call_ready_pct) else "-")
            out.append(f"| {p} | {f(r.warm_pct)} | {f(r.batch_full_warm_pct)} | "
                       f"{f(r.cold_pct_hideable)} / {f(r.cold_pct_nonhideable)} | "
                       f"{f(r.added_ms_mean, '.0f')} | {f(r.idle_pod_s_per_1k, '.0f')} | "
                       f"{f(r.cost_usd_per_1k, '.4f')} | {f(r.prewarm_used_pct)} | "
                       f"{f(r.nfr3_unused_over_always_warm, '.3f')} | {fr2} |")
        out.append("")
    h = pd.read_csv(HIDE_CSV, dtype={"vocab": str}, keep_default_na=False)
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


def main() -> int:
    df = latest()
    label = str(df.cold_start_label.iloc[-1])
    pareto(df, label)
    tau_curve(df, label)
    (RESULTS / "sim_report.md").write_text(report(df, label), encoding="utf-8")
    print("wrote results/sim_pareto.png, results/tau_curve.png, results/sim_report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
