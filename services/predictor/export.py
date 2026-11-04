"""Build a model bundle for the service from one source's train/val split.

    nice -n 10 python -m services.predictor.export --source tracelab --cold-start PATH
    # -> models/lgbm-tracelab-<commit>/ (not committed; see models/README.md)

The ranker is fitted on train (val for early stopping and the n-gram weights), with the
seeds in configs/model/ranker.yaml, so the same data and seed give the same files. The
policy is configs/model/decide.yaml as shipped. golden.json holds 20 val decision points and
this bundle's answers to them. The cold-start file is not part of the bundle: the service
reads it by path at startup (``COLD_START_PATH``).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from predictors.cold_start import ColdStarts
from predictors.data import Episode
from predictors.decide import CONFIG as DECIDE_CONFIG
from predictors.eval import git_commit, split_groups
from predictors.lgbm_ranker import LGBMRanker
from services.predictor.engine import (
    BUNDLE_SCHEMA,
    REQUIRED,
    golden,
    golden_requests,
    load_bundle,
    read_folder,
    sha256,
)

ROOT = Path(__file__).resolve().parents[2]


def export(source: str, vocab: str | None, out: Path, cold: ColdStarts,
           version: str | None = None) -> Path:
    train, val, _ = split_groups(source, "fixed", vocab=vocab)
    commit = git_commit()
    return build(train, val, out / (version or f"lgbm-{source}-{commit[:12]}"), cold,
                 source, vocab, commit)


def build(train: Sequence[Episode], val: Sequence[Episode], folder: Path, cold: ColdStarts,
          source: str, vocab: str | None = None, commit: str = "", decide: Path = DECIDE_CONFIG
          ) -> Path:
    """Fit the ranker and write a verified bundle to ``folder`` (its name is the version)."""
    ranker = LGBMRanker.from_config().fit(train, val)
    version = folder.name
    ranker.save(folder)
    (folder / "decide.yaml").write_bytes(decide.read_bytes())
    files = {n: (folder / n).read_bytes() for n in REQUIRED if n not in ("MANIFEST.json",
                                                                         "golden.json")}
    man = {"schema_version": BUNDLE_SCHEMA, "model_version": version, "source": source,
           "vocab_map": vocab, "git_commit": commit, "vocabulary": sorted(ranker.vocab),
           "files": {}}
    # golden answers come from the bundle as loaded (same code path as the service)
    tmp = {**files, "golden.json": b"[]", "MANIFEST.json": b""}
    man["files"] = {n: sha256(b) for n, b in tmp.items() if n != "MANIFEST.json"}
    tmp["MANIFEST.json"] = json.dumps(man).encode()
    b = load_bundle(tmp, cold, check_golden=False)
    gold = json.dumps(golden(b, golden_requests(val)), sort_keys=True, indent=1) + "\n"
    (folder / "golden.json").write_text(gold, encoding="utf-8")
    man["files"] = {n: sha256((folder / n).read_bytes()) for n in REQUIRED
                    if n != "MANIFEST.json"}
    (folder / "MANIFEST.json").write_text(json.dumps(man, sort_keys=True, indent=1) + "\n",
                                          encoding="utf-8")
    load_bundle(read_folder(folder), cold)  # round trip, golden included
    return folder


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--source", default="tracelab")
    p.add_argument("--vocab", default=None)
    p.add_argument("--out", default=str(ROOT / "models"))
    p.add_argument("--cold-start", default=str(ROOT / "configs" / "cold_start.yaml"))
    p.add_argument("--version", default=None)
    a = p.parse_args(argv)
    folder = export(a.source, a.vocab, Path(a.out), ColdStarts.load(Path(a.cold_start)),
                    a.version)
    print(f"wrote {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
