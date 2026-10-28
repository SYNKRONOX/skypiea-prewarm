"""Build small, text-free samples for git from the raw data.

    python tools/make_sample.py tracelab        # one dataset
    python tools/make_sample.py --all           # every dataset that is downloaded

Writes data/samples/<dataset>_sample.jsonl.gz with the first 200 sessions (fewer if the
file would exceed 5 MB). Tool-call datasets are converted to the ``traces.schema`` format
and keep only tool names, timing, errors, batch structure and a group id. No prompt,
message, argument or result text is kept. Huawei cold starts are kept as numeric rows.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import sys
import tarfile
import zipfile
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from traces.schema import Batch, Call, Session  # noqa: E402

RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "samples"
MAX_BYTES = 5 * 1024 * 1024
DEFAULT_N = 200


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def ms_between(a: datetime, b: datetime) -> float:
    return round((b - a).total_seconds() * 1000, 3)


def tracelab(n: int) -> Iterator[dict[str, Any]]:
    """One batch per LLM round that emitted tools; group = user."""
    path = RAW / "tracelab" / "syfi_coding_trace.jsonl.gz"
    order: list[str] = []
    rows: dict[str, list[dict[str, Any]]] = {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            sid = d["session_id"]
            if sid not in rows:
                if len(order) >= n:
                    continue
                order.append(sid)
                rows[sid] = []
            rows[sid].append(
                {
                    "round": d["round_index"],
                    "user": d["user"],
                    "provider": d["provider"],
                    "start": min((e["timestamp"] for e in d["timing_events"]), default=None),
                    "tools": [
                        (t["tool_name"], t.get("emitted_at"), t.get("tool_wall_latency_ms"),
                         t.get("is_error"))
                        for t in d["tools"]
                    ],
                }
            )
    for sid in order:
        rs = sorted(rows[sid], key=lambda r: r["round"])
        starts = [ts(r["start"]) for r in rs if r["start"]]
        t0 = min(starts) if starts else None
        batches = []
        for r in rs:
            if not r["tools"]:
                continue
            emits = [ts(e) for _, e, _, _ in r["tools"] if e]
            t_emit = ms_between(t0, min(emits)) if t0 and emits else 0.0
            calls = [Call(name, float(lat or 0.0), bool(err)) for name, _, lat, err in r["tools"]]
            batches.append(Batch(len(batches), t_emit, calls))
        yield Session(sid, "tracelab", rs[0]["user"], batches).to_dict()


def copilot_2026(n: int) -> Iterator[dict[str, Any]]:
    """One batch per tool_batch; error = status 2. No user id exists, so group = session."""
    archive = sorted((RAW / "copilot_2026").glob("*.tar.gz"))[0]
    count = 0
    with tarfile.open(archive, "r|gz") as tf:
        for m in tf:
            if not (m.isfile() and m.name.endswith(".jsonl.gz")):
                continue
            member = tf.extractfile(m)
            assert member is not None
            with gzip.open(member, "rt", encoding="utf-8") as f:
                for line in f:
                    d = json.loads(line)
                    stamps = [ts(c["timestamp"]) for t in d["turns"] for c in t["llm_calls"]]
                    stamps += [ts(b["timestamp"]) for t in d["turns"] for b in t["tool_batches"]]
                    t0 = min(stamps) if stamps else None
                    batches = []
                    for turn in d["turns"]:
                        for b in turn["tool_batches"]:
                            if not b["function_calls"]:
                                continue
                            calls = [
                                Call(c["name"], float(c["duration_ms"] or 0.0), c["status"] == 2)
                                for c in b["function_calls"]
                            ]
                            t_emit = ms_between(t0, ts(b["timestamp"])) if t0 else 0.0
                            batches.append(Batch(len(batches), t_emit, calls))
                    batches.sort(key=lambda b: b.t_emit_ms)
                    for i, b in enumerate(batches):
                        b.batch_id = i
                    sid = d["session_id"]
                    yield Session(sid, "copilot_2026", sid, batches).to_dict()
                    count += 1
                    if count >= n:
                        return


def tau_bench(n: int) -> Iterator[dict[str, Any]]:
    """tau-bench v1 has no timestamps, so t_emit_ms and service_ms are 0. group = domain:task."""
    count = 0
    for path in sorted((RAW / "tau_bench" / "historical_trajectories").glob("*.json")):
        domain = "airline" if "airline" in path.stem else "retail"
        for t in json.loads(path.read_text(encoding="utf-8")):
            # Tool replies are matched to calls only to read the error flag; text is dropped.
            replies = {
                m.get("tool_call_id"): str(m.get("content") or "").startswith("Error")
                for m in t["traj"]
                if m["role"] == "tool"
            }
            batches = []
            for m in t["traj"]:
                tcs = m.get("tool_calls") or []
                if m["role"] == "assistant" and tcs:
                    calls = [
                        Call(c["function"]["name"], 0.0, replies.get(c["id"], False)) for c in tcs
                    ]
                    batches.append(Batch(len(batches), 0.0, calls))
            sid = f"{path.stem}:{t['task_id']}:{t['trial']}"
            yield Session(sid, "tau_bench", f"{domain}:{t['task_id']}", batches).to_dict()
            count += 1
            if count >= n:
                return


def tau2_bench(n: int) -> Iterator[dict[str, Any]]:
    """Emit time from the assistant message, service time from the matching tool reply."""
    count = 0
    for path in sorted((RAW / "tau2_bench" / "data" / "tau2" / "results").rglob("*.json")):
        domain = next(d for d in ("airline", "retail", "telecom", "mock") if f"_{d}" in path.stem)
        for s in json.loads(path.read_text(encoding="utf-8"))["simulations"]:
            t0 = ts(s["start_time"])
            replies = {
                m["id"]: (ts(m["timestamp"]), bool(m.get("error")))
                for m in s["messages"]
                if m["role"] == "tool"
            }
            batches = []
            for m in s["messages"]:
                tcs = m.get("tool_calls") or []
                if m["role"] != "assistant" or not tcs:
                    continue
                emit = ts(m["timestamp"])
                calls = []
                for c in tcs:
                    done, err = replies.get(c["id"], (emit, False))
                    calls.append(Call(c["name"], max(ms_between(emit, done), 0.0), err))
                batches.append(Batch(len(batches), ms_between(t0, emit), calls))
            yield Session(s["id"], "tau2_bench", f"{domain}:{s['task_id']}", batches).to_dict()
            count += 1
            if count >= n:
                return


def bfcl(n: int) -> Iterator[dict[str, Any]]:
    """Each question is one parallel batch built from the ground-truth answer.

    Takes an equal share from each of the four categories so that all are represented.
    """
    files = sorted((RAW / "bfcl").glob("possible_answer_*.json"))
    per_file = max(n // len(files), 1)
    for path in files:
        category = path.stem.removeprefix("possible_answer_BFCL_v4_")
        with path.open(encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= per_file:
                    break
                d = json.loads(line)
                calls = [Call(name) for g in d["ground_truth"] for name in g]
                batch = Batch(0, 0.0, calls)
                yield Session(d["id"], f"bfcl:{category}", d["id"], [batch]).to_dict()


HUAWEI_COLS = [
    "day", "time", "clusterName", "funcName", "userID", "totalCost_cold_start",
    "podAllocationCost", "deployCodeCost", "deployDependencyCost", "schedulingCost",
]


def huawei_coldstart(n: int) -> Iterator[dict[str, Any]]:
    """Numeric cold-start rows; podID is reduced to its pool (CPU and memory class)."""
    count = 0
    with zipfile.ZipFile(RAW / "huawei_coldstart" / "R3.zip") as z:
        for info in sorted(z.infolist(), key=lambda i: i.filename):
            if not info.filename.endswith(".csv"):
                continue
            with z.open(info) as raw:
                for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8")):
                    out: dict[str, Any] = {c: float(row[c]) for c in HUAWEI_COLS}
                    out["pool"] = row["podID"].rsplit("-", 1)[0]
                    yield out
                    count += 1
                    if count >= n:
                        return


BUILDERS: dict[str, Callable[[int], Iterator[dict[str, Any]]]] = {
    "tracelab": tracelab,
    "copilot_2026": copilot_2026,
    "tau_bench": tau_bench,
    "tau2_bench": tau2_bench,
    "bfcl": bfcl,
    "huawei_coldstart": huawei_coldstart,
}


def write(dataset: str, n: int) -> Path:
    """Write the sample, shrinking ``n`` until the file is under 5 MB."""
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"{dataset}_sample.jsonl.gz"
    while True:
        buf = io.BytesIO()
        # mtime=0 keeps the gzip bytes identical across runs, so git sees no spurious change.
        with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
            k = 0
            for rec in BUILDERS[dataset](n):
                gz.write((json.dumps(rec, separators=(",", ":")) + "\n").encode("utf-8"))
                k += 1
        size = buf.tell()
        if size <= MAX_BYTES or n <= 1:
            break
        n = max(int(n * MAX_BYTES / size * 0.9), 1)
    if size > MAX_BYTES:
        raise SystemExit(f"{dest.name} is {size / 1024 / 1024:.2f} MB, over the 5 MB limit")
    dest.write_bytes(buf.getvalue())
    rel = dest.relative_to(ROOT).as_posix()
    print(f"  {dataset:18} {k:4d} records  {size / 1024:8.1f} KB  {rel}")
    return dest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("dataset", nargs="?", choices=sorted(BUILDERS))
    p.add_argument("--all", action="store_true", help="every dataset present in data/raw/")
    p.add_argument("--n", type=int, default=DEFAULT_N, help="sessions per sample (default 200)")
    args = p.parse_args(argv)
    if not args.dataset and not args.all:
        p.error("give a dataset or --all")
    names = [args.dataset] if args.dataset else [d for d in BUILDERS if (RAW / d).exists()]
    for name in names:
        write(name, args.n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
