"""Build small, text-free samples for git.

    python tools/make_sample.py tracelab        # one dataset
    python tools/make_sample.py --all           # every dataset that is available

Tool-call sources are sampled from the pipeline output (data/processed/<source>/), so run
``python -m traces.pipeline --source <name>`` first. Each sample keeps up to 200 sessions,
split evenly across train, val and test (fewer if the file would exceed 5 MB), in the
common schema with its ``split`` field. Huawei cold starts are sampled from the raw zip as
numeric rows. Writes data/samples/<dataset>_sample.jsonl.gz.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import sys
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from traces.load import iter_session_dicts  # noqa: E402

RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
OUT = ROOT / "data" / "samples"
MAX_BYTES = 5 * 1024 * 1024
DEFAULT_N = 200
SESSION_SOURCES = ["tracelab", "copilot_2026", "tau_bench", "tau2_bench", "bfcl"]


def processed(source: str) -> Callable[[int], Iterator[dict[str, Any]]]:
    """First n/3 sessions of each split, in pipeline order."""

    def build(n: int) -> Iterator[dict[str, Any]]:
        per_split = max(n // 3, 1)
        for split in ("train", "val", "test"):
            for i, d in enumerate(iter_session_dicts(source, split, PROCESSED)):
                if i >= per_split:
                    break
                yield d

    return build


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
    **{s: processed(s) for s in SESSION_SOURCES},
    "huawei_coldstart": huawei_coldstart,
}


def available(name: str) -> bool:
    if name == "huawei_coldstart":
        return (RAW / name).exists()
    return (PROCESSED / name / "sessions.jsonl.gz").exists()


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
        if size <= MAX_BYTES or n <= 3:
            break
        n = max(int(n * MAX_BYTES / size * 0.9), 3)
    if size > MAX_BYTES:
        raise SystemExit(f"{dest.name} is {size / 1024 / 1024:.2f} MB, over the 5 MB limit")
    dest.write_bytes(buf.getvalue())
    rel = dest.relative_to(ROOT).as_posix()
    print(f"  {dataset:18} {k:4d} records  {size / 1024:8.1f} KB  {rel}")
    return dest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("dataset", nargs="?", choices=sorted(BUILDERS))
    p.add_argument("--all", action="store_true", help="every dataset that is available")
    p.add_argument("--n", type=int, default=DEFAULT_N, help="sessions per sample (default 200)")
    args = p.parse_args(argv)
    if not args.dataset and not args.all:
        p.error("give a dataset or --all")
    names = [args.dataset] if args.dataset else [d for d in BUILDERS if available(d)]
    for name in names:
        write(name, args.n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
