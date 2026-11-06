"""The model bundle the service serves, and the ``/decide`` answer.

A bundle is a folder (``models/<version>/``) of JSON / YAML / LightGBM-text files:

- ``MANIFEST.json``: ``{schema_version, model_version, files: {name: sha256}, vocabulary,
  git_commit, created}``; every other file must be listed with its sha256.
- ``ranker.lgb``, ``calibrator.json``, ``ranker_meta.json``, ``counts.json``: the LightGBM
  re-ranker and its transition graph (``LGBMRanker.save``).
- ``decide.yaml``: the warm-decision policy (``DecideConfig``), as val picked it.
- ``golden.json``: requests and the answers this bundle must give (checked before a swap).

``load_bundle`` reads bytes only (``read_folder`` for a local folder); verification checks
hashes, schema version, the vocabulary against the ranker and the graph, the decision config,
and the golden answers. A ``Bundle`` is immutable after load: a request keeps a reference to
the bundle it started with, so a swap never changes an answer in flight.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

import yaml

from predictors.base import Decision, NextToolModel
from predictors.cold_start import ColdStarts
from predictors.data import END, Context, Episode
from predictors.decide import DecideConfig, Decider
from predictors.graph import TransitionGraph
from predictors.ladder import Ladder
from predictors.lgbm_ranker import LGBMRanker, _fsum_normalise
from services.predictor.schema import Parsed

BUNDLE_SCHEMA = 1
REQUIRED = ("MANIFEST.json", *LGBMRanker.FILES, "decide.yaml", "golden.json")
MAX_FILE_BYTES = 64 * 1024 * 1024
TOP_K = 5  # ranked entries returned (FR-1: at least 3)
END_OUT = "END"


class BundleError(ValueError):
    """The bundle cannot be served; the message is safe to return (no file contents)."""


class _ScoredOnce(NextToolModel):
    """The ranker for ONE request: scores the candidates once and serves both the FR-1
    distribution (``predict``) and the in-next-batch probabilities (``batch_probs``) from
    them. Created per request, never shared, so there is no cache across requests."""

    name = "ranker"

    def __init__(self, ranker: LGBMRanker) -> None:
        self.ranker = ranker
        self._ctx: Context | None = None
        self._scores: dict[str, float] = {}

    def _get(self, ctx: Context) -> dict[str, float]:
        if self._ctx is not ctx:
            self._scores, self._ctx = self.ranker.scores(ctx), ctx
        return self._scores

    def fit(self, train: Sequence[Episode], val: Sequence[Episode]) -> _ScoredOnce:
        return self

    def predict(self, ctx: Context, k: int = 1) -> dict[str, float]:
        return _fsum_normalise(self._get(ctx))  # exactly LGBMRanker.ranked

    def batch_probs(self, ctx: Context) -> dict[str, float]:
        return {t: p for t, p in self._get(ctx).items() if t != END}


class GraphMajority(NextToolModel):
    """Last rung before "none": the train unigram distribution of the graph."""

    name = "majority"

    def __init__(self, graph: TransitionGraph) -> None:
        uni = graph.tables.get("h1", {}).get("", {})
        tot = math.fsum(uni.values())
        self.p = {t: v / tot for t, v in uni.items()} if tot > 0 else {}
        self.mean_batch_size = graph.mean_batch_size

    def fit(self, train: Sequence[Episode], val: Sequence[Episode]) -> GraphMajority:
        return self

    def predict(self, ctx: Context, k: int = 1) -> dict[str, float]:
        if not self.p:
            raise ValueError("empty graph")
        return dict(self.p)


@dataclass(frozen=True)
class Bundle:
    version: str
    manifest: Mapping[str, Any]
    ranker: LGBMRanker
    cfg: DecideConfig
    ladder: Ladder
    decider: Decider
    golden: tuple[dict[str, Any], ...] = ()  # golden requests (warm-up before a swap)
    cold_match: bool = True  # served with the cold-start file the bundle was exported with

    @property
    def vocabulary(self) -> tuple[str, ...]:
        return tuple(self.manifest["vocabulary"])


DECIDE_OVERRIDE_KEYS = ("lookahead",)


def with_decide_overrides(b: Bundle, overrides: Mapping[str, Any]) -> Bundle:
    """The bundle served with decide-config overrides (live console: lookahead=2). Applied
    AFTER the bundle was verified with its own decide.yaml (golden answers); only the keys in
    DECIDE_OVERRIDE_KEYS are allowed. Empty overrides return the bundle unchanged."""
    if not overrides:
        return b
    bad = set(overrides) - set(DECIDE_OVERRIDE_KEYS)
    if bad:
        raise BundleError(f"decide overrides not allowed: {sorted(bad)}")
    cfg = b.cfg.with_(**overrides)
    if cfg.lookahead not in (1, 2):
        raise BundleError("lookahead must be 1 or 2")
    return replace(b, cfg=cfg, decider=Decider(b.ladder, b.decider.graph, b.decider.cold, cfg))


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def read_folder(folder: Path) -> dict[str, bytes]:
    """The manifest's files from a local folder: regular files only, size-capped."""
    files: dict[str, bytes] = {}
    man = folder / "MANIFEST.json"
    if not man.is_file():
        raise BundleError("no MANIFEST.json")
    files["MANIFEST.json"] = _read(man)
    try:
        names = list(json.loads(files["MANIFEST.json"])["files"])
    except (ValueError, KeyError, TypeError) as e:
        raise BundleError("MANIFEST.json is not a valid manifest") from e
    for n in names:
        if not isinstance(n, str) or "/" in n or "\\" in n or n.startswith("."):
            raise BundleError("manifest lists an invalid file name")
        p = folder / n
        if p.is_symlink() or not p.is_file():
            raise BundleError(f"missing file {n}")
        files[n] = _read(p)
    return files


def _read(p: Path) -> bytes:
    if p.stat().st_size > MAX_FILE_BYTES:
        raise BundleError(f"file {p.name} is over {MAX_FILE_BYTES} bytes")
    return p.read_bytes()


def decide_config(text: bytes) -> DecideConfig:
    d = yaml.safe_load(text.decode("utf-8"))
    if not isinstance(d, dict):
        raise BundleError("decide.yaml is not a mapping")
    names = {f.name for f in fields(DecideConfig)}
    if set(d) - names:
        raise BundleError("decide.yaml has unknown keys")
    if "cost_tau_clamp" in d:
        d["cost_tau_clamp"] = tuple(d["cost_tau_clamp"])
    cfg = DecideConfig(**d)
    if cfg.threshold not in ("fixed", "cost") or cfg.start_rule not in ("timing", "now"):
        raise BundleError("decide.yaml has an invalid rule")
    return cfg


def load_bundle(files: Mapping[str, bytes], cold: ColdStarts, *, check_golden: bool = True
                ) -> Bundle:
    for n in REQUIRED:
        if n not in files:
            raise BundleError(f"missing file {n}")
    try:
        man = json.loads(files["MANIFEST.json"].decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise BundleError("MANIFEST.json is not JSON") from e
    if not isinstance(man, dict) or man.get("schema_version") != BUNDLE_SCHEMA:
        raise BundleError("unsupported bundle schema_version")
    version = man.get("model_version")
    if not isinstance(version, str) or not 0 < len(version) <= 64:
        raise BundleError("model_version must be a string of 1-64 characters")
    listed = man.get("files")
    if not isinstance(listed, dict) or set(listed) != set(REQUIRED) - {"MANIFEST.json"}:
        raise BundleError("manifest must list exactly the bundle files")
    for n, h in listed.items():
        if sha256(files[n]) != h:
            raise BundleError(f"sha256 mismatch for {n}")
    try:
        ranker = LGBMRanker.from_files(files)
        cfg = decide_config(files["decide.yaml"])
    except BundleError:
        raise
    except Exception as e:  # noqa: BLE001 - any parse error refuses the bundle
        raise BundleError(f"cannot load the model: {type(e).__name__}") from e
    assert ranker.base is not None
    vocab = man.get("vocabulary")
    if (not isinstance(vocab, list) or sorted(vocab) != sorted(ranker.vocab)
            or sorted(vocab) != sorted(ranker.base.graph.vocab)):
        raise BundleError("vocabulary does not match the ranker and the graph")
    ladder = Ladder([("ranker", ranker), ("ngram", ranker.base.ngram),
                     ("majority", GraphMajority(ranker.base.graph))], budget_ms=cfg.budget_ms)
    try:
        cases = json.loads(files["golden.json"].decode("utf-8"))
        reqs = tuple(c["request"] for c in cases)
    except (ValueError, UnicodeDecodeError, KeyError, TypeError) as e:
        raise BundleError("golden.json is not a list of cases") from e
    # decisions depend on the cold-start file; with another file only the ranking is checked
    made_with = man.get("cold_start_sha256")
    match = made_with is None or made_with == cold.sha256
    b = Bundle(version, man, ranker, cfg, ladder, Decider(ladder, ranker.base.graph, cold, cfg),
               reqs, match)
    if check_golden:
        verify_golden(b, files["golden.json"], ranked_only=not match)
    return b


# --------------------------------------------------------------------------- answers
def _ranked(probs: Mapping[str, float]) -> list[dict[str, Any]]:
    """Top-K of a next-tool distribution, sorted, sum <= 1 (FR-1), END as ``END``."""
    items = sorted(((t, float(p)) for t, p in probs.items() if p > 0), key=lambda kv:
                   (-kv[1], kv[0]))[:TOP_K]
    s = math.fsum(p for _, p in items)
    scale = 1.0 if s <= 1.0 else (1.0 - 1e-12) / s
    out = [{"tool_class": END_OUT if t == END else t, "p": round(min(1.0, p * scale), 6)}
           for t, p in items]
    while out and math.fsum(r["p"] for r in out) > 1.0:  # rounding up can overshoot
        out[0]["p"] = round(out[0]["p"] - 1e-6, 6)
    return out


def answer(b: Bundle, parsed: Parsed) -> dict[str, Any]:
    """The contract response for one parsed request (latency added by the caller)."""
    ctx = Context.of(parsed.events)
    once = _ScoredOnce(b.ranker)
    lad = Ladder([(n, once if n == "ranker" else m) for n, m in b.ladder.rungs],
                 budget_ms=b.ladder.budget_ms)
    probs, rmode, rdeg = lad.ranked(ctx)
    # decisions come from the rung that answered ``ranked`` (or a later one), so a budget
    # fallback never mixes ranker decisions with an n-gram distribution
    names = [n for n, _ in lad.rungs]
    if rmode not in names:
        d = Decision({}, "none", True)
    else:
        rest = Ladder(lad.rungs[names.index(rmode):], budget_ms=lad.budget_ms)
        d = Decider(rest, b.decider.graph, b.decider.cold, b.cfg).decide(ctx)
        if rdeg and not d.degraded:
            d = Decision(d.warm, d.mode, True)
    decisions = [{"tool_class": t, "warm": True, "copies": int(w.copies),
                  "start_in_ms": int(max(0, round(w.start_in_ms))),
                  "ttl_s": int(min(600, max(0, math.ceil(w.ttl_ms / 1000.0)))),
                  "prob": round(min(1.0, max(0.0, float(w.prob))), 6)}
                 for t, w in sorted(d.warm.items())]
    ranked = _ranked(probs)
    warm = {x["tool_class"] for x in decisions}
    decisions += [{"tool_class": r["tool_class"], "warm": False, "copies": 0, "start_in_ms": 0,
                   "ttl_s": None, "prob": r["p"]}
                  for r in ranked[:3] if r["tool_class"] not in warm and r["tool_class"] != END_OUT
                  and d.mode not in ("none",)]
    return {"ranked": ranked, "decisions": decisions, "model_version": b.version,
            "keep_after_use_s": int(round(b.cfg.keep_after_use_s)), "mode": d.mode,
            "degraded": bool(d.degraded or rdeg or rmode != "ranker"),
            "truncated": parsed.truncated}


def empty_answer(version: str, truncated: bool = False, keep_s: int | None = None
                 ) -> dict[str, Any]:
    out: dict[str, Any] = {"ranked": [], "decisions": [], "model_version": version,
                           "mode": "none", "degraded": True, "truncated": truncated}
    if keep_s is not None:
        out["keep_after_use_s"] = keep_s
    return out


def golden_requests(eps: Sequence[Episode], n: int = 20) -> list[dict[str, Any]]:
    """Contract requests built from the first ``n`` decision points of ``eps`` (metadata only:
    class names, times, batch ids)."""
    out = []
    for ep in eps:
        for i in range(1, ep.n_batches + 1):
            pre = ep.context(i).prefix()
            out.append({"session_id": f"golden-{len(out)}", "now_ms": 0.0, "history": [
                {"tool_class": e.tool, "t_start_ms": float(e.t_offset_ms or 0.0),
                 "t_end_ms": float(e.t_offset_ms or 0.0) + float(e.service_ms or 0.0),
                 "error": bool(e.error), "batch_id": int(e.batch_id)} for e in pre]})
            if len(out) >= n:
                return out
    return out


def golden(b: Bundle, requests: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    from services.predictor.schema import parse_request
    b = unbudgeted(b)
    return [{"request": r, "response": answer(b, parse_request(r))} for r in requests]


def unbudgeted(b: Bundle) -> Bundle:
    """The same bundle with no ladder time budget, so golden answers do not depend on load."""
    lad = Ladder(b.ladder.rungs, budget_ms=math.inf)
    return Bundle(b.version, b.manifest, b.ranker, b.cfg, lad,
                  Decider(lad, b.decider.graph, b.decider.cold, b.cfg), b.golden)


def verify_golden(b: Bundle, text: bytes, ranked_only: bool = False) -> None:
    from services.predictor.schema import parse_request
    b = unbudgeted(b)
    try:
        cases = json.loads(text.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise BundleError("golden.json is not JSON") from e
    if not isinstance(cases, list) or not cases:
        raise BundleError("golden.json has no cases")
    for c in cases:
        got = answer(b, parse_request(c["request"]))
        want = {k: v for k, v in c["response"].items() if k != "model_version"}
        got.pop("model_version")
        if ranked_only:
            got, want = got.get("ranked"), want.get("ranked")
        if json.dumps(got, sort_keys=True) != json.dumps(want, sort_keys=True):
            raise BundleError("golden answers differ")


def warmup_p99_ms(b: Bundle, requests: Sequence[dict[str, Any]], n: int = 200) -> float:
    from services.predictor.schema import parse_request
    parsed = [parse_request(r) for r in requests]
    lat = []
    for i in range(n):
        t0 = time.perf_counter()
        answer(b, parsed[i % len(parsed)])
        lat.append((time.perf_counter() - t0) * 1000.0)
    lat.sort()
    return lat[min(len(lat) - 1, math.ceil(0.99 * len(lat)) - 1)]
