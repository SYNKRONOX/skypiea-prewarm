"""Download Skypiea datasets into data/raw/<dataset>/ and record them in data/MANIFEST.json.

    python data/download.py --list
    python data/download.py --dataset tracelab --dry-run
    python data/download.py --dataset tracelab

Every file's size is checked before downloading. Files over 200 MB are refused unless
``--yes-large`` is passed. Downloads stream to a ``.part`` file, resume if interrupted,
and are verified by size and hashed with sha256. Re-running is safe: finished files are
verified and hashed, not downloaded again. Gated datasets are never downloaded; the script
prints how to request access instead. Uses only the standard library and ``requests``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
MANIFEST = ROOT / "MANIFEST.json"
LARGE_BYTES = 200 * 1024 * 1024
CHUNK = 1 << 20
TIMEOUT = 60

GORILLA = (
    "https://raw.githubusercontent.com/ShishirPatil/gorilla/"
    "6ea57973c7a6097fd7c5915698c54c17c5b1b6c8/berkeley-function-call-leaderboard/bfcl_eval/data"
)
BFCL_FILES = [
    "BFCL_v4_parallel.json",
    "BFCL_v4_parallel_multiple.json",
    "BFCL_v4_live_parallel.json",
    "BFCL_v4_live_parallel_multiple.json",
]
COPILOT_RELEASE = "Azure/AzurePublicDataset/releases/tags/ghcp-coding-agent-2026"

# Each entry is one of:
#   files: list of {url, name} downloaded over HTTP
#   git:   list of {url, dir, commit, exclude, include} shallow clones; exclude drops path
#          prefixes from a sparse checkout and include adds single files back
#   gated: instructions only, nothing is downloaded
DATASETS: dict[str, dict[str, Any]] = {
    "tracelab": {
        "source": "https://github.com/uw-syfi/TraceLab/releases/tag/v0.0.2",
        "licence": "CC BY 4.0",
        "files": [
            {
                "url": "https://github.com/uw-syfi/TraceLab/releases/download/v0.0.2/"
                "syfi_coding_trace.jsonl.gz",
                "name": "syfi_coding_trace.jsonl.gz",
            }
        ],
    },
    "bfcl": {
        "source": "https://github.com/ShishirPatil/gorilla/tree/main/berkeley-function-call-leaderboard",
        "licence": "Apache-2.0",
        "files": [{"url": f"{GORILLA}/{f}", "name": f} for f in BFCL_FILES]
        + [
            {"url": f"{GORILLA}/possible_answer/{f}", "name": f"possible_answer_{f}"}
            for f in BFCL_FILES
        ],
    },
    "tau_bench": {
        "source": "https://github.com/sierra-research/tau-bench, "
        "https://github.com/sierra-research/tau2-bench",
        "licence": "MIT",
        "git": [
            {
                "url": "https://github.com/sierra-research/tau-bench",
                "dir": "tau_bench",
                "commit": "59a200c6d575d595120f1cb70fea53cef0632f6b",
                "exclude": [],
            },
            {
                "url": "https://github.com/sierra-research/tau2-bench",
                "dir": "tau2_bench",
                "commit": "122e2432b82d740e8f313f46f0a071729c404f39",
                # The full checkout is 948 MB, mostly published results. Keep code and
                # domains plus the smallest trajectory file (7.2 MB).
                "exclude": [
                    "data/tau2/results/",
                    "data/tau2/multilingual/",
                    "data/tau2/multilingual_extensions/",
                    "data/voice/",
                    "web/",
                ],
                "include": [
                    "data/tau2/results/final/"
                    "o4-mini-2025-04-16_airline_default_gpt-4.1-2025-04-14_4trials.json",
                ],
            },
        ],
    },
    "copilot_2026": {
        "source": "https://github.com/Azure/AzurePublicDataset/releases/tag/ghcp-coding-agent-2026",
        "licence": "CC BY 4.0",
        "release": COPILOT_RELEASE,
        "files": [
            {
                "url": "https://github.com/Azure/AzurePublicDataset/releases/download/"
                "ghcp-coding-agent-2026/date.2026-06-06.tar.gz",
                "name": "date.2026-06-06.tar.gz",
            }
        ],
    },
    "huawei_coldstart": {
        "source": "https://github.com/sir-lab/data-release/blob/main/README_data_release_2025.md",
        "licence": "CC BY 4.0",
        "files": [
            {
                "url": "https://drive.usercontent.google.com/download"
                "?id=1WlUk-tQLao6mU0FoMx9YO2-HIMevEDjX&export=download",
                "name": "R3.zip",
            }
        ],
    },
    "toolathlon": {
        "source": "https://huggingface.co/datasets/hkust-nlp/Toolathlon",
        "licence": "Benchmark-only (gated)",
        "gated": (
            "Toolathlon is gated on Hugging Face and is not downloaded by this script.\n"
            "  1. Log in to Hugging Face and open https://huggingface.co/datasets/hkust-nlp/Toolathlon\n"
            "     (trajectories: hkust-nlp/Toolathlon-Trajectories).\n"
            "  2. Read the licence and click 'Agree and access repository'.\n"
            "  3. Before using it, ask the course instructor whether fitting our predictors'\n"
            "     transition-count tables on it counts as training, which the benchmark-only\n"
            "     licence may forbid. Until then, use it for evaluation only.\n"
            "  4. Download it manually into data/raw/toolathlon/. Never commit HF tokens."
        ),
    },
}


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} GB"


def remote_size(session: requests.Session, url: str) -> int:
    """Remote file size in bytes, from HEAD or a 1-byte range request."""
    r = session.head(url, allow_redirects=True, timeout=TIMEOUT)
    if r.ok and "content-length" in r.headers and "text/html" not in r.headers.get(
        "content-type", ""
    ):
        return int(r.headers["content-length"])
    r = session.get(url, headers={"Range": "bytes=0-0"}, stream=True, timeout=TIMEOUT)
    r.close()
    if r.status_code == 206 and "content-range" in r.headers:
        return int(r.headers["content-range"].rsplit("/", 1)[1])
    if "text/html" in r.headers.get("content-type", ""):
        raise RuntimeError(f"{url} returned an HTML page, not a file (login or confirm page?)")
    raise RuntimeError(f"cannot determine size of {url} (HTTP {r.status_code})")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def fetch(session: requests.Session, url: str, dest: Path, size: int) -> None:
    """Stream ``url`` to ``dest``, resuming from ``dest.part`` if present."""
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with session.get(url, headers=headers, stream=True, timeout=TIMEOUT) as r:
        r.raise_for_status()
        if "text/html" in r.headers.get("content-type", ""):
            raise RuntimeError(f"{url} returned an HTML page, not a file")
        mode = "ab" if have and r.status_code == 206 else "wb"
        done = have if mode == "ab" else 0
        if have:
            print(f"    resuming at {human(done)}" if mode == "ab" else "    restarting")
        with part.open(mode) as f:
            for chunk in r.iter_content(CHUNK):
                f.write(chunk)
                done += len(chunk)
                print(f"\r    {human(done)} / {human(size)}", end="", flush=True)
    print()
    got = part.stat().st_size
    if got != size:
        if got > size:
            part.unlink()  # cannot be resumed
        raise RuntimeError(f"size mismatch for {dest.name}: got {got}, expected {size}")
    part.replace(dest)


def load_manifest() -> dict[str, Any]:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {"files": {}}


def save_manifest(m: dict[str, Any]) -> None:
    m["files"] = dict(sorted(m["files"].items()))
    MANIFEST.write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8")


def record(m: dict[str, Any], key: str, entry: dict[str, Any]) -> None:
    old = m["files"].get(key, {})
    unchanged = all(old.get(k) == entry.get(k) for k in ("bytes", "sha256", "commit"))
    entry["downloaded"] = old["downloaded"] if old and unchanged else str(dt.date.today())
    m["files"][key] = entry


def do_files(name: str, spec: dict[str, Any], args: argparse.Namespace, m: dict[str, Any]) -> None:
    out = RAW / name
    with requests.Session() as s:
        s.headers["User-Agent"] = "skypiea-prewarm-downloader"
        # Ask for the bytes as stored; otherwise Content-Length is the gzip-encoded size.
        s.headers["Accept-Encoding"] = "identity"
        plan = [(f, remote_size(s, f["url"])) for f in spec["files"]]
        for f, size in plan:
            flag = "  (over 200 MB)" if size > LARGE_BYTES else ""
            print(f"  {f['name']}: {human(size)}{flag}")
        if args.dry_run:
            return
        too_big = [f["name"] for f, size in plan if size > LARGE_BYTES]
        if too_big and not args.yes_large:
            raise SystemExit(f"Refusing files over 200 MB without --yes-large: {too_big}")
        out.mkdir(parents=True, exist_ok=True)
        for f, size in plan:
            dest = out / f["name"]
            if dest.exists() and dest.stat().st_size == size:
                print(f"  {f['name']}: already present, verifying")
            else:
                if dest.exists():
                    print(f"  {f['name']}: size differs from remote, downloading again")
                    dest.unlink()
                print(f"  {f['name']}: downloading")
                fetch(s, f["url"], dest, size)
            digest = sha256_of(dest)
            print(f"    ok  {human(size)}  sha256={digest[:16]}...")
            record(
                m,
                dest.relative_to(ROOT.parent).as_posix(),
                {"dataset": name, "url": f["url"], "licence": spec["licence"],
                 "bytes": size, "sha256": digest},
            )


def github_tree_bytes(
    url: str, commit: str, exclude: list[str], include: list[str]
) -> tuple[int, int]:
    """Files and bytes a checkout of ``commit`` would contain (public API, no token)."""
    repo = url.removeprefix("https://github.com/")
    r = requests.get(
        f"https://api.github.com/repos/{repo}/git/trees/{commit}?recursive=1", timeout=TIMEOUT
    )
    r.raise_for_status()
    blobs = [
        b for b in r.json()["tree"]
        if b["type"] == "blob"
        and (b["path"] in include or not any(b["path"].startswith(e) for e in exclude))
    ]
    return len(blobs), sum(b["size"] for b in blobs)


def dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and ".git" not in p.parts)


def git(*cmd: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *cmd], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def do_git(name: str, spec: dict[str, Any], args: argparse.Namespace, m: dict[str, Any]) -> None:
    for repo in spec["git"]:
        dest = RAW / repo["dir"]
        include = repo.get("include", [])
        n, size = github_tree_bytes(repo["url"], repo["commit"], repo["exclude"], include)
        note = f", excluding {', '.join(repo['exclude'])}" if repo["exclude"] else ""
        note += f", plus {len(include)} included file(s)" if include else ""
        flag = "  (over 200 MB)" if size > LARGE_BYTES else ""
        print(f"  {repo['dir']}: {n} files, {human(size)}{note}{flag}")
        if args.dry_run:
            continue
        if dest.exists() and (dest / ".git").exists():
            print("    already cloned")
        else:
            if size > LARGE_BYTES and not args.yes_large:
                raise SystemExit(f"Refusing {repo['dir']} ({human(size)}) without --yes-large")
            dest.mkdir(parents=True, exist_ok=True)
            git("init", "-q", cwd=dest)
            git("remote", "add", "origin", repo["url"], cwd=dest)
            fetch_args = ["fetch", "-q", "--depth", "1"]
            if repo["exclude"]:
                git("config", "core.sparseCheckout", "true", cwd=dest)
                git("config", "core.sparseCheckoutCone", "false", cwd=dest)
                patterns = ["/*"] + [f"!/{e}" for e in repo["exclude"]] + [f"/{i}" for i in include]
                (dest / ".git" / "info" / "sparse-checkout").write_text("\n".join(patterns) + "\n")
                # Blobless fetch: only blobs that are checked out get downloaded.
                fetch_args.append("--filter=blob:none")
            print("    cloning (depth 1)")
            git(*fetch_args, "origin", repo["commit"], cwd=dest)
            git("checkout", "-q", "FETCH_HEAD", cwd=dest)
        commit = git("rev-parse", "HEAD", cwd=dest)
        on_disk = dir_bytes(dest)
        print(f"    ok  commit={commit[:12]}  {human(on_disk)} on disk")
        record(
            m,
            dest.relative_to(ROOT.parent).as_posix() + "/",
            {"dataset": name, "url": repo["url"], "licence": spec["licence"],
             "bytes": on_disk, "commit": commit, "exclude": repo["exclude"], "include": include},
        )


def list_release(release: str) -> None:
    r = requests.get(f"https://api.github.com/repos/{release}", timeout=TIMEOUT)
    r.raise_for_status()
    assets = sorted(r.json()["assets"], key=lambda a: a["size"])
    print(f"  all release assets ({len(assets)}, {human(sum(a['size'] for a in assets))}):")
    for a in assets:
        print(f"    {human(a['size']):>9}  {a['name']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--dataset", choices=sorted(DATASETS), help="dataset to download")
    p.add_argument("--list", action="store_true", help="list datasets and their status")
    p.add_argument("--dry-run", action="store_true", help="show sizes, download nothing")
    p.add_argument("--yes-large", action="store_true", help="allow files over 200 MB")
    args = p.parse_args(argv)

    if args.list or not args.dataset:
        m = load_manifest()
        for name, spec in DATASETS.items():
            have = [k for k, v in m["files"].items() if v["dataset"] == name]
            status = "gated" if "gated" in spec else (f"{len(have)} recorded" if have else "-")
            print(f"{name:18} {spec['licence']:24} {status:12} {spec['source']}")
        return 0

    spec = DATASETS[args.dataset]
    print(f"{args.dataset}  [{spec['licence']}]  {spec['source']}")
    if "gated" in spec:
        print(spec["gated"])
        return 0
    if "release" in spec:
        list_release(spec["release"])
        print("  selected for download:")
    m = load_manifest()
    try:
        if "git" in spec:
            if shutil.which("git") is None:
                raise SystemExit("git is required for this dataset")
            do_git(args.dataset, spec, args, m)
        else:
            do_files(args.dataset, spec, args, m)
    finally:
        if not args.dry_run:
            save_manifest(m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
