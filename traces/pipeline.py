"""Convert one raw dataset into the common schema, split it by group and write it out.

    python -m traces.pipeline --source tracelab
    python -m traces.pipeline --source copilot_2026 --shards 1
    python -m traces.pipeline --source tau_bench --limit 100

Stages: read -> convert -> map tools -> clean -> split -> write -> validate -> report.
Outputs in data/processed/<source>/:

    sessions.jsonl.gz   one Session dict per line, plus a "split" field
    calls.parquet       one row per call
    splits.json         group -> train / val / test (reused by every later step)
    report.md           what was converted, dropped and how it is split

Sessions are streamed: pass 1 converts and cleans into a temporary file while collecting
groups, then splits are assigned, then pass 2 writes the outputs.
"""

from __future__ import annotations

import argparse
import gzip
import importlib
import json
import math
import random
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import chain, islice
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from traces.schema import Session
from traces.tool_classes import ToolMapper, load_mapper

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

SEED = 0
TEST_FRAC = 0.3
VAL_FRAC = 0.2  # of the train groups
MAX_TEXT = 64
MAX_SERVICE_MS = 3_600_000.0  # one hour; longer tool runs are clipped and counted

# Per-source settings. BFCL items are single parallel batches, so they keep 1 batch.
SOURCES: dict[str, dict[str, Any]] = {
    "tracelab": {"min_batches": 2},
    "copilot_2026": {"min_batches": 2},
    "tau_bench": {"min_batches": 2},
    "tau2_bench": {"min_batches": 2},
    "bfcl": {"min_batches": 1},
    "toolathlon": {"min_batches": 2},
}

CALL_SCHEMA = pa.schema([
    ("session_id", pa.string()),
    ("source", pa.string()),
    ("group", pa.string()),
    ("split", pa.string()),
    ("has_timing", pa.bool_()),
    ("batch_id", pa.int32()),
    ("position_in_batch", pa.int32()),
    ("batch_size", pa.int32()),
    ("t_offset_ms", pa.float64()),
    ("service_ms", pa.float64()),
    ("tool", pa.string()),
    ("raw_tool", pa.string()),
    ("error", pa.bool_()),
])


class ValidationError(RuntimeError):
    pass


@dataclass
class Clean:
    """Cleaning step. Counts everything it changes or drops."""

    min_batches: int
    stats: Counter[str]

    def __post_init__(self) -> None:
        self.seen: set[str] = set()

    def __call__(self, s: Session) -> Session | None:
        st = self.stats
        st["sessions_in"] += 1
        if not any(b.calls for b in s.batches):
            st["dropped_empty"] += 1
            return None
        if s.session_id in self.seen:
            st["dropped_duplicate_id"] += 1
            return None
        self.seen.add(s.session_id)
        if s.has_timing:
            if any(b.t_offset_ms is None for b in s.batches):
                raise ValidationError(f"{s.session_id}: has_timing but a batch has no time")
            order = [b.batch_id for b in s.batches]
            s.batches.sort(key=lambda b: b.t_offset_ms)  # stable: ties keep source order
            if [b.batch_id for b in s.batches] != order:
                st["sessions_reordered_by_time"] += 1
        for i, b in enumerate(s.batches):
            b.batch_id = i
            if b.t_offset_ms is not None and b.t_offset_ms < 0:
                st["clipped_negative_t_offset"] += 1
                b.t_offset_ms = 0.0
            for c in b.calls:
                if c.service_ms is None:
                    continue
                if c.service_ms < 0:
                    st["clipped_negative_service"] += 1
                    c.service_ms = 0.0
                elif c.service_ms > MAX_SERVICE_MS:
                    st["clipped_service_over_1h"] += 1
                    c.service_ms = MAX_SERVICE_MS
        if sum(1 for b in s.batches if b.calls) < self.min_batches:
            st[f"dropped_fewer_than_{self.min_batches}_batches"] += 1
            return None
        st["sessions_kept"] += 1
        return s


def assign_splits(groups: list[str], seed: int = SEED) -> dict[str, str]:
    """70/30 train/test by group, then 20% of train groups become validation."""
    gs = sorted(groups)
    random.Random(seed).shuffle(gs)
    n_test = round(len(gs) * TEST_FRAC)
    test, train = gs[:n_test], gs[n_test:]
    n_val = round(len(train) * VAL_FRAC)
    out = {g: "test" for g in test}
    out.update({g: "val" for g in train[:n_val]})
    out.update({g: "train" for g in train[n_val:]})
    return out


def read_sessions(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)


def quantiles(xs: list[float]) -> str:
    if not xs:
        return "n/a"
    a = np.asarray(xs)
    return f"median {np.median(a):,.0f} ms, p95 {np.percentile(a, 95):,.0f} ms (n={len(a):,})"


def run(source: str, limit: int | None = None, raw: Path = RAW, out_root: Path = PROCESSED,
        **opts: Any) -> str:
    if source not in SOURCES:
        raise SystemExit(f"unknown source {source!r}; choose from {sorted(SOURCES)}")
    out = out_root / source
    out.mkdir(parents=True, exist_ok=True)
    conv_stats: Counter[str] = Counter()
    clean_stats: Counter[str] = Counter()
    converter = importlib.import_module(f"traces.convert_{source}").convert
    sessions = islice(converter(raw, conv_stats, **opts), limit)
    first = next(sessions, None)  # surfaces converter errors (e.g. gated data) first
    mapper: ToolMapper = load_mapper(source)
    clean = Clean(SOURCES[source]["min_batches"], clean_stats)

    # Pass 1: read -> convert -> map tools -> clean, into a temporary file.
    tmp = out / "_cleaned.jsonl.gz"
    groups: Counter[str] = Counter()
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        for s in chain([first] if first is not None else [], sessions):
            for b in s.batches:
                for c in b.calls:
                    c.raw_tool = c.raw_tool or c.tool
                    c.tool = mapper(c.raw_tool)
            kept = clean(s)
            if kept is not None:
                groups[kept.group] += 1
                f.write(json.dumps(kept.to_dict(), separators=(",", ":")) + "\n")

    # Split by group.
    splits = assign_splits(list(groups))
    (out / "splits.json").write_text(json.dumps(
        {"seed": SEED, "test_frac": TEST_FRAC, "val_frac_of_train": VAL_FRAC,
         "groups": dict(sorted(splits.items()))}, indent=1) + "\n", encoding="utf-8")

    # Pass 2: write sessions and calls, collecting report statistics.
    n_sess, n_batches, n_calls, n_parallel = Counter(), 0, 0, 0
    calls_split: Counter[str] = Counter()
    cls_freq, raw_tools = Counter(), set()
    gaps, thinks, services = [], [], []
    errors, timed_sessions = 0, 0
    rows: dict[str, list[Any]] = {name: [] for name in CALL_SCHEMA.names}
    writer = pq.ParquetWriter(out / "calls.parquet", CALL_SCHEMA)

    def flush() -> None:
        if rows["session_id"]:
            writer.write_table(pa.table(rows, schema=CALL_SCHEMA))
            for v in rows.values():
                v.clear()

    with gzip.open(out / "sessions.jsonl.gz", "wt", encoding="utf-8") as fout:
        for d in read_sessions(tmp):
            split = splits[d["group"]]
            d["split"] = split
            fout.write(json.dumps(d, separators=(",", ":")) + "\n")
            s = Session.from_dict(d)
            n_sess[split] += 1
            timed_sessions += s.has_timing
            prev_end = prev_emit = None
            for b in s.batches:
                if not b.calls:
                    continue
                n_batches += 1
                n_parallel += b.is_parallel
                for pos, c in enumerate(b.calls):
                    n_calls += 1
                    calls_split[split] += 1
                    cls_freq[c.tool] += 1
                    raw_tools.add(c.raw_tool)
                    errors += c.error
                    if c.service_ms is not None:
                        services.append(c.service_ms)
                    for k, v in (("session_id", s.session_id), ("source", s.source),
                                 ("group", s.group), ("split", split),
                                 ("has_timing", s.has_timing), ("batch_id", b.batch_id),
                                 ("position_in_batch", pos), ("batch_size", len(b.calls)),
                                 ("t_offset_ms", b.t_offset_ms), ("service_ms", c.service_ms),
                                 ("tool", c.tool), ("raw_tool", c.raw_tool),
                                 ("error", c.error)):
                        rows[k].append(v)
                if s.has_timing and b.t_offset_ms is not None:
                    if prev_emit is not None:
                        gaps.append(b.t_offset_ms - prev_emit)
                        if prev_end is not None:
                            thinks.append(max(b.t_offset_ms - prev_end, 0.0))
                    known = [c.service_ms for c in b.calls if c.service_ms is not None]
                    prev_emit = b.t_offset_ms
                    prev_end = b.t_offset_ms + max(known) if known else None
                if len(rows["session_id"]) >= 200_000:
                    flush()
    flush()
    writer.close()
    tmp.unlink()

    validate(out, mapper)

    # Report.
    split_groups = Counter(splits.values())
    lines = [
        f"# {source}: pipeline report",
        "",
        f"Limit: {limit if limit is not None else 'none'}"
        + (f"; options: {opts}" if opts else ""),
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Sessions | {sum(n_sess.values()):,} |",
        f"| Batches | {n_batches:,} |",
        f"| Calls | {n_calls:,} |",
        f"| Avg calls per batch | {n_calls / max(n_batches, 1):.2f} |",
        f"| Parallel batches (2+ calls) | {n_parallel / max(n_batches, 1):.1%} |",
        f"| Calls with error | {errors / max(n_calls, 1):.1%} |",
        f"| Unique raw tools | {len(raw_tools):,} |",
        f"| Tool classes used | {len(cls_freq)} of {len(mapper.classes)} |",
        f"| Sessions with timing | {timed_sessions:,} |",
        f"| Inter-batch gap (emit to emit) | {quantiles(gaps)} |",
        f"| Think time (last result to next emit) | {quantiles(thinks)} |",
        f"| Tool run time (service_ms) | {quantiles(services)} |",
        "",
        "## Splits (by group, seed 0)",
        "",
        "| Split | Groups | Sessions | Calls |",
        "|---|---|---|---|",
    ]
    for sp in ("train", "val", "test"):
        lines.append(f"| {sp} | {split_groups[sp]:,} | {n_sess[sp]:,} | {calls_split[sp]:,} |")
    lines += ["", "## Tool classes", "", "| Class | Calls | Share |", "|---|---|---|"]
    for cls, n in cls_freq.most_common():
        lines.append(f"| {cls} | {n:,} | {n / max(n_calls, 1):.1%} |")
    other = cls_freq[mapper.default] / max(n_calls, 1)
    if other > 0.2:
        lines += ["", f"**Warning:** '{mapper.default}' covers {other:.1%} of calls (over 20%)."]
    lines += ["", "## Cleaning and converter notes", "", "| Item | Count |", "|---|---|"]
    for k, v in sorted({**clean_stats, **conv_stats}.items()):
        lines.append(f"| {k} | {v:,} |")
    report = "\n".join(lines) + "\n"
    (out / "report.md").write_text(report, encoding="utf-8")
    return report


def validate(out: Path, mapper: ToolMapper) -> None:
    """Re-read the outputs and fail loudly on any broken invariant."""
    splits = json.loads((out / "splits.json").read_text(encoding="utf-8"))["groups"]
    seen_split: dict[str, str] = {}
    classes = set(mapper.classes)
    n_calls = 0
    for d in read_sessions(out / "sessions.jsonl.gz"):
        sid, g = d["session_id"], d["group"]
        if seen_split.setdefault(g, d["split"]) != d["split"] or splits[g] != d["split"]:
            raise ValidationError(f"group {g!r} appears in more than one split")
        for name in ("session_id", "source", "group", "trace_type"):
            if len(str(d[name])) > MAX_TEXT:
                raise ValidationError(f"{sid}: field {name} longer than {MAX_TEXT} chars")
        prev_t = -math.inf
        for i, b in enumerate(d["batches"]):
            if b["batch_id"] != i:
                raise ValidationError(f"{sid}: batch_ids do not increase 0..n-1")
            if b["node"] is not None and len(b["node"]) > MAX_TEXT:
                raise ValidationError(f"{sid}: node longer than {MAX_TEXT} chars")
            if d["has_timing"]:
                if b["t_offset_ms"] is None or b["t_offset_ms"] < prev_t:
                    raise ValidationError(f"{sid}: t_offset_ms decreases or is missing")
                prev_t = b["t_offset_ms"]
            for c in b["calls"]:
                n_calls += 1
                if c["tool"] not in classes:
                    raise ValidationError(f"{sid}: tool {c['tool']!r} is not a known class")
                if len(c["raw_tool"] or "") > MAX_TEXT:
                    raise ValidationError(f"{sid}: raw_tool longer than {MAX_TEXT} chars")
    rows = pq.ParquetFile(out / "calls.parquet").metadata.num_rows
    if rows != n_calls:
        raise ValidationError(f"calls.parquet has {rows} rows, sessions have {n_calls} calls")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--source", required=True, choices=sorted(SOURCES))
    p.add_argument("--limit", type=int, help="convert at most N sessions")
    p.add_argument("--shards", type=int, help="copilot_2026: read at most N shard files")
    args = p.parse_args(argv)
    opts = {"shards": args.shards} if args.shards is not None else {}
    try:
        print(run(args.source, args.limit, **opts))
    except ValidationError as e:
        print(f"VALIDATION FAILED: {e}", file=sys.stderr)
        return 1
    except (RuntimeError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
