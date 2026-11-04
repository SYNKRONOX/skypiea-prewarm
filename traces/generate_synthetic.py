"""Synthetic PRD traces: deterministic (Scenario B), branching (Scenario A), random walk.

    python -m traces.generate_synthetic                      # all three, 2,000 sessions each
    python -m traces.generate_synthetic --type branching --n 500
    python -m traces.generate_synthetic --type prd           # the demo mix (A + B + C)

Sessions are sampled from the workflow graphs in configs/model/workflow_graphs.yaml. Every
event carries its workflow ``node``; nodes with ``tool: null`` (e.g. classify_intent) become
node-only events, i.e. PRD events with ``tool_called: null``.

Timing is never invented from thin air: think times (end of one event to the start of the
next) are drawn from the empirical think-time distribution of a real source's TRAIN split
(``think_source`` in the graph file: tau2_bench for the support scenario, tracelab for the
coding ones), and tool run times from TraceLab's train ``service_ms``. Both are sampled by
inverse CDF over 1,001 quantiles.

``prd`` is a SYNTHETIC mix for the demo bundle: ``n`` Scenario A (branching), ``n`` Scenario B
(deterministic) and ``n // 4`` Scenario C (spike: one promo_code_lookup call; the PRD gives no
graph for C, see workflow_graphs.yaml) sessions in one source, synthetic_prd. Each part keeps
its own random stream and session ids (syn_<kind>_<i>), so it is not evidence about real
workloads: it shows the mechanism on the PRD scenarios.

Output goes through the normal pipeline (traces/pipeline.py) to
data/processed/synthetic_<type>/ with the same files as a real source (sessions.jsonl.gz,
calls.parquet, splits.json, report.md). Groups are session ids. Seed 0 by default; the same
seed and input data give identical files.
"""

from __future__ import annotations

import argparse
import sys
import zlib
from collections import Counter
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from traces import pipeline
from traces.load import PROCESSED, load_calls_df
from traces.schema import Batch, Call, Session

ROOT = Path(__file__).resolve().parent.parent
GRAPHS = ROOT / "configs" / "model" / "workflow_graphs.yaml"
TYPES = ("deterministic", "branching", "random_walk", "prd")
DEFAULT_TYPES = ("deterministic", "branching", "random_walk")
PRD_MIX = (("branching", 1.0), ("deterministic", 1.0), ("spike", 0.25))
END = "<end>"
N_QUANTILES = 1001


def load_graphs(path: Path = GRAPHS) -> dict[str, dict[str, Any]]:
    """The scenario graphs, checked: probabilities sum to 1 and every target exists."""
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    scenarios = cfg["scenarios"]
    for name, sc in scenarios.items():
        nodes = sc["nodes"]
        for node, spec in nodes.items():
            total = sum(spec["next"].values())
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"{name}.{node}: next probabilities sum to {total}")
            for tgt in spec["next"]:
                if tgt != END and tgt not in nodes:
                    raise ValueError(f"{name}.{node}: unknown successor {tgt!r}")
        if sc["start"] != "uniform" and sc["start"] not in nodes:
            raise ValueError(f"{name}: unknown start node {sc['start']!r}")
    return scenarios


@lru_cache(maxsize=8)
def _quantiles(source: str, what: str, root: Path = PROCESSED) -> np.ndarray:
    """Quantile grid of think times or service times from ``source``'s train split."""
    df = load_calls_df(source, "train", root=root)
    if what == "service":
        x = df["service_ms"].dropna().to_numpy(dtype=float)
    else:
        b = (df.groupby(["session_id", "batch_id"], sort=True)
               .agg(t=("t_offset_ms", "first"), svc=("service_ms", "max"))
               .reset_index())
        b = b.dropna(subset=["t"])
        b["svc"] = b["svc"].fillna(0.0)
        prev_end = (b["t"] + b["svc"]).groupby(b["session_id"]).shift(1)
        x = (b["t"] - prev_end).dropna().clip(lower=0.0).to_numpy(dtype=float)
    if len(x) == 0:
        raise RuntimeError(f"no {what} times in {source} train split")
    return np.quantile(x, np.linspace(0.0, 1.0, N_QUANTILES))


def _draw(grid: np.ndarray, rng: np.random.Generator) -> float:
    return float(np.interp(rng.random(), np.linspace(0.0, 1.0, len(grid)), grid))


def _walk(sc: dict[str, Any], rng: np.random.Generator) -> list[str]:
    nodes = sc["nodes"]
    names = sorted(nodes)
    node = names[rng.integers(len(names))] if sc["start"] == "uniform" else sc["start"]
    path = [node]
    for _ in range(int(sc.get("max_events", 200)) - 1):
        nxt = nodes[node]["next"]
        keys = list(nxt)
        node = keys[rng.choice(len(keys), p=np.asarray([nxt[k] for k in keys]))]
        if node == END:
            break
        path.append(node)
    return path


def sessions(kind: str, n: int, seed: int = 0, root: Path = PROCESSED,
             graphs: dict[str, dict[str, Any]] | None = None) -> Iterator[Session]:
    """``n`` synthetic sessions of one type, deterministic for a given seed."""
    sc = (graphs or load_graphs())[kind]
    think = _quantiles(sc["think_source"], "think", root)
    service = _quantiles("tracelab", "service", root)
    rng = np.random.default_rng([seed, zlib.crc32(kind.encode())])
    source = f"synthetic_{kind}"
    for i in range(n):
        t, batches = 0.0, []
        for j, node in enumerate(_walk(sc, rng)):
            if j:
                t += _draw(think, rng)
            tool = sc["nodes"][node]["tool"]
            calls = []
            if tool is not None:
                svc = round(_draw(service, rng), 3)
                calls = [Call(tool, svc, False, tool)]
            batches.append(Batch(j, round(t, 3), node, calls))
            t += calls[0].service_ms if calls else 0.0
        sid = f"syn_{kind}_{i:05d}"
        yield Session(sid, source, sid, True, batches, sc["trace_type"])


def generate(kind: str, n: int = 2000, seed: int = 0, root: Path = PROCESSED,
             out_root: Path = PROCESSED) -> str:
    if kind not in TYPES:
        raise SystemExit(f"unknown type {kind!r}; choose from {TYPES}")

    def converter(raw: Path, stats: Counter[str], **_: Any) -> Iterator[Session]:
        stats["synthetic_seed"] = seed
        if kind != "prd":
            yield from sessions(kind, n, seed, root)
            return
        for part, frac in PRD_MIX:
            for sess in sessions(part, int(n * frac), seed, root):
                sess.source = "synthetic_prd"
                yield sess

    return pipeline.run(f"synthetic_{kind}", out_root=out_root, converter=converter)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--type", choices=TYPES, action="append",
                   help="trace type (repeatable); default: the three single types")
    p.add_argument("--n", type=int, default=2000, help="sessions per type")
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args(argv)
    for kind in args.type or DEFAULT_TYPES:
        print(generate(kind, args.n, args.seed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
