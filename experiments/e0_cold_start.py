"""E0: real cold-start measurement of the Skypiea MCP tool servers (kind or AWS k3s).

    python -m experiments.e0_cold_start --target kind --n 10 --modes cached,uncached --tools all
    python -m experiments.e0_cold_start --target kind --n 10 --parallel 3     # 3 pods at once

Per tool (or group of --parallel tools), mode and repetition (order shuffled per round, seed 0):
  uncached: delete ALL tool images from every schedulable node (kind: docker exec <node>
            crictl rmi; aws: ssh <vm> sudo k3s crictl rmi) so the pod must pull every layer
            (tool images share base and dependency layers; the probe uses another base);
  cached:   image already on the node (a tool's uncached run goes first in each round, so the
            image is present; the kubelet event confirms which case actually happened -> mode_ok).
The run itself (scale 0 -> 1, pod watch, /healthz on the pod IP, first MCP call through the
Service, warm calls, scale -> 0, wait until the pod is gone) happens inside the cluster, in the
e0-probe pod (experiments/e0_probe/probe.py), on one monotonic clock.

Phases (ms; they sum to total):
  scheduling      scale request -> PodScheduled observed
  image_pull      kubelet "Successfully pulled ... in X (Y including waiting)": Y, so time
                  queued behind other pulls counts (0 if the image was present)
  container_start remainder until the process started (sandbox, network, unpack, create)
  app_init        process start -> /healthz answers on the pod IP (imports, data/model load)
  readiness_lag   /healthz OK -> pod Ready=True observed (kubelet probe period 1 s)
  first_call      Ready -> first successful MCP call through the Service (the probe's client is
                  warmed up before t0, so only routing + the server's first request count;
                  a 100 ms TCP gate keeps dropped SYNs from stalling an attempt). With the MCP SDK
                  2.3.0 client pinned to protocol 2026-07-28 a call is two stateless POSTs
                  (tools/call, then tools/list for output-schema validation); no initialize.
  total           scale request -> first successful response

Noise control: before every timed run the node machine's 1-min load average, available memory
and number of running containers are recorded. With --require-quiet (default) a run waits up to
60 s for load1 <= 60% of the cores, then runs anyway and is flagged noisy=True. Summaries report
medians over all runs and over quiet runs only.

kind pulls from a local registry container: its image_pull is a LOWER BOUND (no network
distance). AWS pulls from ECR in the same region: the real number.

Outputs results/e0_<target>[_p<K>]_runs.csv, ..._summary.csv, ....png. Numbers are reported as
measured: nothing is smoothed, clipped or dropped. Failed runs stay in the runs CSV with an
`error` and are excluded (and counted) in the summary.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CFG_PATH = ROOT / "experiments" / "e0_config.yaml"
PHASES = ["scheduling", "image_pull", "container_start", "app_init", "readiness_lag",
          "first_call"]
METRICS = PHASES + ["total", "warm_median"]
HUAWEI = ROOT / "data" / "processed" / "huawei_coldstart" / "coldstart.parquet"
PULL_SOURCE = {"kind": "local registry, lower bound", "aws": "ECR same region"}
QUIET_FRACTION = 0.6
QUIET_WAIT_S = 60


def sh(args: list[str], check: bool = True,
       timeout: float | None = 600) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=check, timeout=timeout)


class Target:
    """kubectl access, image deletion and load stats, per backend."""

    def __init__(self, name: str, cfg: dict, args: argparse.Namespace):
        self.name, self.ns = name, cfg["namespace"]
        if name == "kind":
            self.kubectl = ["kubectl", "--context", cfg["kind"]["context"]]
        else:
            kc = os.path.expanduser(args.kubeconfig or cfg["aws"]["kubeconfig"])
            self.kubectl = ["kubectl", "--kubeconfig", kc]
            self.ssh_host = args.ssh_host or self._tf_output("instance_ip")
            self.ssh = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                        "-i", os.path.expanduser(args.ssh_key or cfg["aws"]["ssh_key"]),
                        f"{cfg['aws']['ssh_user']}@{self.ssh_host}"]

    @staticmethod
    def _tf_output(key: str) -> str:
        return sh(["terraform", f"-chdir={ROOT / 'infra' / 'aws'}", "output", "-raw", key]).stdout

    def k(self, *args: str, check: bool = True, timeout: float | None = 600):
        return sh([*self.kubectl, "-n", self.ns, *args], check=check, timeout=timeout)

    def schedulable_nodes(self) -> list[str]:
        nodes = json.loads(sh([*self.kubectl, "get", "nodes", "-o", "json"]).stdout)["items"]
        return [n["metadata"]["name"] for n in nodes
                if not any(t["effect"] == "NoSchedule" for t in n["spec"].get("taints") or [])]

    def node_cmd(self, node: str, cmd: list[str]) -> subprocess.CompletedProcess:
        if self.name == "kind":
            return sh(["docker", "exec", node, *cmd], check=False)
        return sh([*self.ssh, "sudo", "k3s", *cmd], check=False)  # single-node k3s

    def rmi(self, image: str) -> str:
        msgs = []
        for node in self.schedulable_nodes():
            p = self.node_cmd(node, ["crictl", "rmi", image])
            out = (p.stdout + p.stderr).strip().replace("\n", " ")
            absent = any(s in out.lower() for s in ("not found", "no such image"))
            if p.returncode != 0 and not absent:
                raise RuntimeError(f"crictl rmi {image} on {node} failed: {out}")
            msgs.append(f"{node}:{'deleted' if p.returncode == 0 else 'absent'}")
        return ";".join(msgs)

    def rmi_all_tools(self, keep: str) -> int:
        """Remove every skypiea image (any tag, incl. old probe images) from every schedulable
        node except `keep` (the running probe image). Returns how many images were removed."""
        n = 0
        for node in self.schedulable_nodes():
            p = self.node_cmd(node, ["crictl", "images", "-o", "json"])
            if p.returncode != 0:
                raise RuntimeError(f"crictl images on {node} failed: {p.stderr.strip()}")
            for img in json.loads(p.stdout).get("images", []):
                tags = [t for t in img.get("repoTags") or [] if "/skypiea/" in t]
                if tags and keep not in tags:
                    r = self.node_cmd(node, ["crictl", "rmi", img["id"]])
                    if r.returncode != 0:
                        raise RuntimeError(f"crictl rmi {tags} on {node}: {r.stderr.strip()}")
                    n += 1
        return n

    def stats(self) -> dict:
        """Load on the machine that runs the node(s): the laptop for kind, the VM for AWS."""
        if self.name == "kind":
            with open("/proc/meminfo") as f:
                mem = {ln.split(":")[0]: ln.split()[1] for ln in f}
            running = sh(["docker", "ps", "-q"], check=False).stdout.split()
            return {"load1": round(os.getloadavg()[0], 2), "cpus": os.cpu_count(),
                    "mem_avail_mb": int(mem["MemAvailable"]) // 1024,
                    "running_containers": len(running)}
        p = sh([*self.ssh, "nproc; cut -d' ' -f1 /proc/loadavg; "
                "awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo; "
                "sudo k3s crictl ps -q | wc -l"], check=True)
        cpus, load1, mem, running = p.stdout.split()
        return {"load1": float(load1), "cpus": int(cpus), "mem_avail_mb": int(mem),
                "running_containers": int(running)}

    def wait_quiet(self, require: bool) -> dict:
        t = time.monotonic()
        st = self.stats()
        while require and st["load1"] > QUIET_FRACTION * st["cpus"] \
                and time.monotonic() - t < QUIET_WAIT_S:
            time.sleep(5)
            st = self.stats()
        st["noisy"] = st["load1"] > QUIET_FRACTION * st["cpus"]
        st["quiet_wait_s"] = round(time.monotonic() - t, 1)
        return st


PULLED_RX = re.compile(r"in ([\d.]+)(ms|s) \(([\d.]+)(ms|s) including waiting\)")


def pull_times(r: dict) -> tuple[float | None, float | None]:
    """(pull_ms, pull_incl_wait_ms): stored values, else parsed from the kubelet's Pulled event."""
    pull, wait = r.get("pull_ms"), r.get("pull_incl_wait_ms")
    ev = r.get("pull_event") or ""
    if pull is None and "already present" in ev:
        return 0.0, None
    if pull is None and (m := PULLED_RX.search(ev)):
        f = {"ms": 1.0, "s": 1000.0}
        return float(m.group(1)) * f[m.group(2)], float(m.group(3)) * f[m.group(4)]
    return pull, wait


def phases(r: dict) -> dict:
    """Phase durations (ms) from the probe's single-clock timestamps; None if unavailable.

    image_pull includes time queued behind other pulls (kubelet pulls serially), when known.
    app_init is process start -> the probe's first /healthz OK on the pod IP. Where the server
    also reported its own startup time, the two agree within 11 ms at p90 on kind (680 runs).
    """
    need = ["t_scheduled", "t_proc_start", "t_health_ok", "t_ready", "t_first_ok"]
    pull_ms, pull_wait_ms = pull_times(r)
    if any(r.get(k) is None for k in need) or pull_ms is None:
        return {m: None for m in PHASES + ["total"]}
    pull = float(pull_ms if pull_wait_ms is None else pull_wait_ms)
    return {
        "scheduling": r["t_scheduled"],
        "image_pull": pull,
        "container_start": round(r["t_proc_start"] - r["t_scheduled"] - pull, 2),
        "app_init": round(r["t_health_ok"] - r["t_proc_start"], 2),
        "readiness_lag": round(r["t_ready"] - r["t_health_ok"], 2),
        "first_call": round(r["t_first_ok"] - r["t_ready"], 2),
        "total": r["t_first_ok"],
    }


RUN_FIELDS = (["git_commit", "run_params", "target", "pull_source", "parallel", "round",
               "group", "tool", "tier", "mode",
               "mode_ok", "noisy", "load1", "cpus", "mem_avail_mb", "running_containers",
               "quiet_wait_s", "node", "pod", "image", "image_digest", "image_bytes_local",
               "pulled_image_bytes", "wall_t0", "rmi"]
              + [f"{p}_ms" for p in PHASES] + ["total_ms", "warm_median_ms", "warm_p95_ms",
              "app_startup_ms", "pull_ms", "pull_incl_wait_ms", "t_scale_ack", "t_pod_seen",
              "t_scheduled", "t_pod_ip", "t_proc_start", "t_running_observed", "t_health_ok",
              "t_ready", "t_first_ok", "first_call_attempts", "tcp_gate_fails", "attempt_log",
              "probe_warmup_ms",
              "health_rtt_ms", "scale_down_ms",
              "pull_event", "warnings", "error"])


def run_group(tgt: Target, tools: list[str], mode: str, probe_image: str,
              cfg: dict) -> list[dict]:
    """One timed run of 1..K tools scaled up at the same moment. Returns one dict per tool."""
    rmi = {t: "" for t in tools}
    if mode == "uncached":
        # Remove EVERY tool image, not only this tool's: tools of a tier share their dependency
        # layers, so a leftover sibling image would keep most layers cached.
        removed = tgt.rmi_all_tools(keep=probe_image)
        rmi = {t: f"removed {removed} tool images (all tags)" for t in tools}
    args = ["exec", "deploy/e0-probe", "--", "python", "/app/probe.py", "run",
            "--namespace", tgt.ns, "--warm", str(cfg["warm_calls"]),
            "--service-ms", str(cfg["service_ms"]), "--timeout", str(cfg["run_timeout_s"])]
    for t in tools:
        args += ["--tool", t]
    p = tgt.k(*args, check=False, timeout=cfg["run_timeout_s"] * 2 + 60)
    try:
        out = json.loads(p.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        msg = f"probe exit {p.returncode}: {(p.stderr or p.stdout)[-400:]}"
        out = [{"tool": t, "error": msg} for t in tools]
    by_tool = {r.get("tool"): r for r in out}
    return [{**by_tool.get(t, {"error": "no result from probe"}), "rmi": rmi[t]} for t in tools]


def _stats(s) -> dict:
    return {"median": s.median(), "p95": s.quantile(0.95), "min": s.min(), "max": s.max()}


def summarise(rows: list[dict]):
    import pandas as pd

    df = pd.DataFrame(rows)
    if "noisy" not in df:
        df["noisy"] = False
    df["noisy"] = df["noisy"].astype("boolean").fillna(False)
    ok = df[df["error"].isna() | (df["error"] == "")]
    out = []
    for (tool, mode), g in ok.groupby(["tool", "mode"], sort=True):
        quiet = g[~g["noisy"]]
        rec = {"tool": tool, "tier": g["tier"].iloc[0], "mode": mode, "n": len(g),
               "n_noisy": int(g["noisy"].sum()),
               "n_failed": int(((df["tool"] == tool) & (df["mode"] == mode)).sum() - len(g)),
               "n_mode_mismatch": int((g["mode_ok"] == False).sum())}  # noqa: E712
        for m in METRICS:
            s = pd.to_numeric(g[f"{m}_ms"], errors="coerce").dropna()
            for stat, v in _stats(s).items():
                rec[f"{m}_{stat}_ms"] = round(float(v), 1) if len(s) else None
        sq = pd.to_numeric(quiet["total_ms"], errors="coerce").dropna()
        rec["total_median_quiet_ms"] = round(float(sq.median()), 1) if len(sq) else None
        rec["total_p95_quiet_ms"] = round(float(sq.quantile(0.95)), 1) if len(sq) else None
        out.append(rec)
    return pd.DataFrame(out), ok


# Reference categorical palette (dataviz skill, light mode), slots in fixed order.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
INK, MUTED, GRID = "#1f1f1e", "#6b6b68", "#e6e6e3"


def plot(summary, target: str, path: Path, parallel: int = 1) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd

    label = f"{target}, pull: {PULL_SOURCE.get(target, '?')}"
    if parallel > 1:
        label += f", {parallel} pods scaled up at once"
    modes = [m for m in ("uncached", "cached") if m in set(summary["mode"])]
    tools = (summary[summary["mode"] == modes[0]].sort_values("total_median_ms")["tool"]
             .tolist())
    x = np.arange(len(tools))
    plt.rcParams.update({"font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                         "xtick.color": INK, "ytick.color": INK, "axes.titlecolor": INK})
    fig, axes = plt.subplots(1 + len(modes), 1, figsize=(12, 4 + 3.4 * len(modes)),
                             constrained_layout=True)
    ax = axes[0]
    w = 0.38
    for i, mode in enumerate(modes):
        s = summary[summary["mode"] == mode].set_index("tool").reindex(tools)
        xs = x + (i - (len(modes) - 1) / 2) * w
        med = pd.to_numeric(s["total_median_ms"], errors="coerce") / 1000
        p95 = pd.to_numeric(s["total_p95_ms"], errors="coerce") / 1000
        ax.bar(xs, med, w * 0.92, color=PALETTE[i], label=f"{mode}: median", zorder=2)
        ax.scatter(xs, p95, marker="_", s=120, color=INK, zorder=3,
                   label="p95" if i == 0 else None)
        ax.vlines(xs, med, p95, color=INK, linewidth=1, zorder=3)
    ax.axhline(1.0, color=MUTED, linewidth=1, linestyle="--", zorder=1)
    ax.text(len(tools) - 0.5, 1.0, " 1 s", va="bottom", ha="right", color=MUTED)
    ax.set_title(f"E0 ({label}): total cold start, scale request → first successful MCP call",
                 loc="left")
    ax.set_ylabel("seconds")
    ax.set_xticks(x, tools, rotation=35, ha="right")
    ax.legend(frameon=False, ncols=3, loc="upper left")
    for mode_i, mode in enumerate(modes):
        ax = axes[1 + mode_i]
        s = summary[summary["mode"] == mode].set_index("tool").reindex(tools)
        bottom = np.zeros(len(tools))
        for j, ph in enumerate(PHASES):
            v = (pd.to_numeric(s[f"{ph}_median_ms"], errors="coerce").fillna(0).clip(lower=0)
                 .to_numpy(dtype=float) / 1000)
            ax.bar(x, v, 0.7, bottom=bottom, color=PALETTE[j], label=ph.replace("_", " "),
                   edgecolor="white", linewidth=1, zorder=2)
            bottom += v
        ax.set_title(f"{mode}: median of each phase (stacked; medians need not sum to the "
                     "median total; negative medians drawn as 0)", loc="left")
        ax.set_ylabel("seconds")
        ax.set_xticks(x, tools, rotation=35, ha="right")
        ax.legend(frameon=False, ncols=6, loc="upper left", fontsize=8)
    for ax in axes:
        ax.grid(axis="y", color=GRID, zorder=0)
        ax.spines[["top", "right"]].set_visible(False)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def verdict(summary, ok, target: str) -> list[str]:
    import pandas as pd

    lines = [f"E0 verdict ({target}; image pull: {PULL_SOURCE.get(target, '?')})"]
    noisy = ok["noisy"] if "noisy" in ok else pd.Series(False, index=ok.index)
    for mode in sorted(ok["mode"].unique()):
        sel = ok["mode"] == mode
        t = pd.to_numeric(ok[sel]["total_ms"], errors="coerce").dropna()
        tq = pd.to_numeric(ok[sel & ~noisy]["total_ms"], errors="coerce").dropna()
        q = (f"; quiet only: median {tq.median():.0f}, p95 {tq.quantile(0.95):.0f} "
             f"(n={len(tq)})") if len(tq) else "; quiet only: none"
        lines.append(f"  {mode:9s} total cold start across tools: median {t.median():.0f} ms, "
                     f"p95 {t.quantile(0.95):.0f} ms (n={len(t)}){q}")
    t_all = pd.to_numeric(ok["total_ms"], errors="coerce").dropna()
    med_ph = {p: pd.to_numeric(ok[f"{p}_ms"], errors="coerce").median() for p in PHASES}
    tot = sum(v for v in med_ph.values() if v and v > 0)
    dom = max(med_ph, key=lambda p: med_ph[p] or 0)
    lines.append("  median phase (all runs): " + ", ".join(
        f"{p} {med_ph[p]:.0f} ms ({100 * (med_ph[p] or 0) / tot:.0f}%)" for p in PHASES))
    lines.append(f"  dominant phase: {dom}")
    lines.append(f"  overall median {t_all.median():.0f} ms -> "
                 f"{'ABOVE' if t_all.median() > 1000 else 'BELOW'} 1 s; "
                 f"noisy runs: {int(noisy.sum())} of {len(ok)}")
    below = summary[summary["total_median_ms"] < 1000][["tool", "mode", "total_median_ms"]]
    if len(below):
        lines.append("  tool/mode with median total < 1 s:")
        for _, r in below.iterrows():
            lines.append(f"    {r['tool']:18s} {r['mode']:9s} {r['total_median_ms']:.0f} ms")
    return lines


def huawei_line(ok) -> str | None:
    if not HUAWEI.exists():
        return None
    import pandas as pd

    h = pd.read_parquet(HUAWEI)
    col = next((c for c in ("totalCost_cold_start", "total_cold_start_s", "cold_start_s")
                if c in h.columns), None)
    if col is None:
        return f"Huawei comparison skipped: no cold-start column in {list(h.columns)[:12]}"
    ours = pd.to_numeric(ok["total_ms"], errors="coerce").median() / 1000
    return (f"Context only: our median total {ours:.3f} s vs Huawei R3 median {col} "
            f"{h[col].median():.3f} (dataset units, n={len(h)}); different systems.")


def groups_for_round(order: list[str], k: int) -> list[list[str]]:
    """Chunks of k tools; the last chunk is topped up from the start of the order."""
    if k <= 1:
        return [[t] for t in order]
    out = [order[i:i + k] for i in range(0, len(order), k)]
    if len(out[-1]) < k:
        out[-1] += [t for t in order if t not in out[-1]][:k - len(out[-1])]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--target", choices=["kind", "aws"], required=True)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--modes", default="cached,uncached")
    ap.add_argument("--tools", default="all")
    ap.add_argument("--parallel", type=int, default=1,
                    help="scale this many different tools up at the same moment")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--require-quiet", action=argparse.BooleanOptionalAction, default=True,
                    help=f"wait up to {QUIET_WAIT_S}s for load1 <= {QUIET_FRACTION:.0%} of "
                         "cores before each run; flag the run noisy otherwise")
    ap.add_argument("--kubeconfig")
    ap.add_argument("--ssh-host")
    ap.add_argument("--ssh-key")
    ap.add_argument("--append", action="store_true",
                    help="append to the existing runs CSV instead of starting a new one")
    ap.add_argument("--report-only", action="store_true",
                    help="re-make summary/figure from the existing runs CSV")
    a = ap.parse_args()
    cfg = yaml.safe_load(CFG_PATH.read_text())
    seed = cfg["seed"] if a.seed is None else a.seed
    res = ROOT / "results"
    res.mkdir(exist_ok=True)
    stem = f"e0_{a.target}" + (f"_p{a.parallel}" if a.parallel > 1 else "")
    runs_csv = res / f"{stem}_runs.csv"
    dirty = sh(["git", "-C", str(ROOT), "status", "--porcelain", "--", "experiments/",
                "tools/", "charts/"], check=False).stdout.strip()
    git_commit = sh(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"]).stdout.strip() \
        + ("-dirty" if dirty else "")
    run_params = (f"n={a.n} modes={a.modes} tools={a.tools} parallel={a.parallel} seed={seed} "
                  f"require_quiet={a.require_quiet} warm_calls={cfg['warm_calls']} "
                  f"service_ms={cfg['service_ms']} timeout_s={cfg['run_timeout_s']}")

    if not a.report_only:
        tgt = Target(a.target, cfg, a)
        servers = yaml.safe_load((ROOT / "tools" / "tool_map.yaml").read_text())["servers"]
        tools = list(servers) if a.tools == "all" else a.tools.split(",")
        # uncached first within each tool, so a cached run always finds the image on the node
        modes = sorted(a.modes.split(","), key=lambda m: m != "uncached")
        deploys = json.loads(tgt.k("get", "deploy", "-l", "app.kubernetes.io/part-of=skypiea",
                                   "-o", "json").stdout)["items"]
        image_of = {d["metadata"]["labels"]["skypiea.io/tool"]:
                    d["spec"]["template"]["spec"]["containers"][0]["image"] for d in deploys
                    if d["metadata"]["name"] != "e0-probe"}
        probe_image = next(d["spec"]["template"]["spec"]["containers"][0]["image"]
                           for d in deploys if d["metadata"]["name"] == "e0-probe")
        missing = [t for t in tools if t not in image_of]
        if missing:
            sys.exit(f"not deployed: {missing}")
        ctl = tgt.k("get", "deploy", "skypiea-controller", "-o", "jsonpath={.spec.replicas}",
                    check=False)
        if ctl.returncode == 0 and ctl.stdout.strip() not in ("", "0"):
            sys.exit("the Skypiea controller is running and would scale tools during E0; "
                     "deploy with --set platform.enabled=false first (make deploy-kind-e0)")
        busy = [d["metadata"]["name"] for d in deploys
                if d["metadata"]["name"] != "e0-probe" and d["spec"].get("replicas")]
        if busy:
            sys.exit(f"not at 0 replicas: {busy}")
        tgt.k("rollout", "status", "deploy/e0-probe", "--timeout=120s")
        local_size = {}
        for t in tools:
            ref = f"skypiea/{t}:{image_of[t].rsplit(':', 1)[1]}"
            p = sh(["docker", "image", "inspect", ref, "--format", "{{.Size}}"], check=False)
            local_size[t] = int(p.stdout) if p.returncode == 0 else None

        rng = random.Random(seed)
        new = not (a.append and runs_csv.exists())
        with runs_csv.open("w" if new else "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=RUN_FIELDS, extrasaction="ignore")
            if new:
                w.writeheader()
            n_groups = len(groups_for_round(tools, a.parallel))
            total_runs = a.n * n_groups * len(modes)
            done = 0
            t_start = time.monotonic()
            for rnd in range(a.n):
                order = tools[:]
                rng.shuffle(order)
                for gi, group in enumerate(groups_for_round(order, a.parallel)):
                    for mode in modes:
                        st = tgt.wait_quiet(a.require_quiet)
                        results = run_group(tgt, group, mode, probe_image, cfg)
                        for tool, r in zip(group, results, strict=True):
                            ph = phases(r)
                            row = {**{k: r.get(k) for k in RUN_FIELDS}, **st, **{
                                "git_commit": git_commit, "run_params": run_params,
                                "target": a.target, "pull_source": PULL_SOURCE[a.target],
                                "parallel": a.parallel, "round": rnd, "group": gi,
                                "tool": tool, "tier": servers[tool]["tier"], "mode": mode,
                                "image": image_of[tool], "image_bytes_local": local_size[tool],
                                "mode_ok": (r.get("image_cached") == (mode == "cached"))
                                if "image_cached" in r else None,
                                "warnings": " | ".join(r.get("warnings", [])) or None,
                                **{f"{k}_ms": v for k, v in ph.items()},
                            }}
                            if row["error"] is None and ph["total"] is None:
                                row["error"] = "incomplete timestamps: " + ",".join(
                                    k for k in ("t_scheduled", "t_proc_start", "t_health_ok",
                                                "t_ready", "t_first_ok", "pull_ms")
                                    if r.get(k) is None)
                            w.writerow(row)
                            print(f"  r{rnd} {tool:18s} {mode:9s} total={row['total_ms']} "
                                  f"pull={row['image_pull_ms']} init={row['app_init_ms']} "
                                  f"mode_ok={row['mode_ok']} load1={st['load1']}"
                                  f"{' NOISY' if st['noisy'] else ''} {row['error'] or ''}",
                                  flush=True)
                        f.flush()
                        done += 1
                        eta = (time.monotonic() - t_start) / done * (total_runs - done)
                        print(f"[{done}/{total_runs} eta {eta / 60:.0f} min]", flush=True)

    import pandas as pd

    rows = pd.read_csv(runs_csv).to_dict("records")
    rows = [{k: (None if (isinstance(v, float) and v != v) else v) for k, v in r.items()}
            for r in rows]
    for r in rows:  # phases are always derived from the raw timestamps, never stored values
        r["report_commit"] = git_commit
        if not r.get("error"):
            ph = phases(r)
            if ph["total"] is None:
                sys.exit(f"refusing to rewrite {runs_csv.name}: run {r.get('tool')} "
                         f"{r.get('mode')} round {r.get('round')} has no derivable phases")
            r["pull_ms"], r["pull_incl_wait_ms"] = pull_times(r)
            r.update({f"{k}_ms": v for k, v in ph.items()})
    summary, ok = summarise(rows)
    summary.insert(0, "pull_source", PULL_SOURCE[a.target])
    meta = pd.read_csv(runs_csv, usecols=["git_commit", "run_params"]).drop_duplicates()
    summary.insert(0, "run_params", " | ".join(meta["run_params"].astype(str)))
    summary.insert(0, "git_commit", " | ".join(meta["git_commit"].astype(str)))
    summary.insert(1, "report_commit", git_commit)
    summary.to_csv(res / f"{stem}_summary.csv", index=False)
    plot(summary, a.target, res / f"{stem}.png", a.parallel)
    cols = ["tool", "mode", "n", "n_noisy", "n_failed", "n_mode_mismatch", "total_median_ms",
            "total_median_quiet_ms", "total_p95_ms", "image_pull_median_ms",
            "app_init_median_ms", "readiness_lag_median_ms", "first_call_median_ms",
            "warm_median_median_ms"]
    with pd.option_context("display.width", 250, "display.max_rows", 100):
        print(summary[cols].to_string(index=False))
    for ln in verdict(summary, ok, a.target):
        print(ln)
    if (h := huawei_line(ok)) is not None:
        print(h)
    # rewrite the runs CSV (raw columns unchanged, phases re-derived) only after the summary
    # and figure succeeded, atomically
    tmp = runs_csv.with_suffix(".csv.tmp")
    pd.DataFrame(rows).to_csv(tmp, index=False)
    os.replace(tmp, runs_csv)
    print(f"wrote results/{stem}_runs.csv, results/{stem}_summary.csv, results/{stem}.png")


if __name__ == "__main__":
    main()
