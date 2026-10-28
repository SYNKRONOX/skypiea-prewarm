"""Fail if any git-tracked (or staged) file is larger than the limit.

GitHub rejects files over 100 MB and warns above 50 MB, so we stop at 50 MB.
Usage: ``python tools/check_sizes.py [--max-mb 50]``.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys


def tracked_files() -> list[str]:
    """Files in the index (tracked plus staged), relative to the repo root."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached"], capture_output=True, check=True
    ).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-mb", type=float, default=50.0)
    args = parser.parse_args(argv)
    limit = int(args.max_mb * 1024 * 1024)

    files = tracked_files()
    offenders = [
        (p, os.path.getsize(p)) for p in files if os.path.isfile(p) and os.path.getsize(p) > limit
    ]
    if offenders:
        print(f"Files over {args.max_mb:g} MB tracked by git:")
        for path, size in sorted(offenders, key=lambda x: -x[1]):
            print(f"  {size / 1024 / 1024:8.1f} MB  {path}")
        print("Remove them from the index (git rm --cached <file>) and add them to .gitignore.")
        return 1
    print(f"OK: {len(files)} tracked files, none over {args.max_mb:g} MB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
