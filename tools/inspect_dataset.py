"""Read-only overview of a downloaded dataset in data/raw/.

    python tools/inspect_dataset.py tracelab

Prints the file list with sizes, the record count, the field names of one record and
three example records. Strings are cut to 80 characters and lists to 3 items, so prompt
or message text is never printed in full.
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
from collections.abc import Iterator
from pathlib import Path
from typing import Any

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
MAX_STR = 80
MAX_LIST = 3
MAX_DEPTH = 4

# Data files per dataset (cloned repos also contain code, which we skip).
GLOBS = {
    "tracelab": ["tracelab/*.jsonl.gz"],
    "bfcl": ["bfcl/*.json"],
    "tau_bench": ["tau_bench/historical_trajectories/*.json"],
    "tau2_bench": ["tau2_bench/data/tau2/results/**/*.json"],
    "copilot_2026": ["copilot_2026/*.tar.gz"],
    "huawei_coldstart": ["huawei_coldstart/*.zip"],
}


def truncate(x: Any, depth: int = 0) -> Any:
    """Shorten strings, lists and deep nesting for display."""
    if isinstance(x, str):
        return x if len(x) <= MAX_STR else x[:MAX_STR] + f"...(+{len(x) - MAX_STR} chars)"
    if depth >= MAX_DEPTH and isinstance(x, (dict, list)):
        return f"<{type(x).__name__} of {len(x)}>"
    if isinstance(x, dict):
        return {k: truncate(v, depth + 1) for k, v in x.items()}
    if isinstance(x, list):
        out = [truncate(v, depth + 1) for v in x[:MAX_LIST]]
        if len(x) > MAX_LIST:
            out.append(f"...(+{len(x) - MAX_LIST} items)")
        return out
    return x


def read_jsonl(f: io.TextIOBase) -> Iterator[dict[str, Any]]:
    for line in f:
        if line.strip():
            yield json.loads(line)


def records(path: Path) -> Iterator[dict[str, Any]]:
    """Yield the records in one data file, whatever its container format."""
    name = path.name
    if name.endswith(".tar.gz"):
        with tarfile.open(path, "r|gz") as tf:
            for m in tf:
                if m.isfile() and m.name.endswith(".jsonl.gz"):
                    member = tf.extractfile(m)
                    assert member is not None
                    with gzip.open(member, "rt", encoding="utf-8") as f:
                        yield from read_jsonl(f)
    elif name.endswith(".jsonl.gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            yield from read_jsonl(f)
    elif name.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            for info in sorted(z.infolist(), key=lambda i: i.filename):
                if info.filename.endswith(".csv"):
                    with z.open(info) as raw:
                        yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8"))
    elif name.endswith(".json"):
        with path.open(encoding="utf-8") as f:
            first = f.read(1)
            f.seek(0)
            if first == "[":
                yield from json.load(f)
            else:
                text = f.read()
                try:
                    obj = json.loads(text)
                except json.JSONDecodeError:  # BFCL files are JSON lines
                    yield from read_jsonl(io.StringIO(text))
                    return
                # tau2-bench results: one object holding a list of simulations.
                yield from obj["simulations"] if "simulations" in obj else [obj]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dataset", choices=sorted(GLOBS))
    p.add_argument("--examples", type=int, default=3)
    args = p.parse_args(argv)

    files = sorted({f for g in GLOBS[args.dataset] for f in RAW.glob(g) if f.is_file()})
    if not files:
        print(f"No files for {args.dataset}. Run: python data/download.py --dataset {args.dataset}")
        return 1

    print(f"== {args.dataset}: {len(files)} file(s)")
    for f in files:
        print(f"  {f.stat().st_size / 1024 / 1024:8.2f} MB  {f.relative_to(RAW).as_posix()}")

    total, examples = 0, []
    for f in files:
        n = 0
        for rec in records(f):
            if len(examples) < args.examples:
                examples.append(rec)
            n += 1
        print(f"  {n:>9,} records  {f.name}")
        total += n
    print(f"Total records: {total:,}")
    if examples:
        print("Fields of one record:", ", ".join(examples[0].keys()))
    for i, rec in enumerate(examples, 1):
        print(f"-- example {i}")
        print(json.dumps(truncate(rec), indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
