"""Vocabulary of the real predictor profiles, and the live-console tasks' coverage of it.

    python -m agent.vocab --image localhost:5001/skypiea/predictor:af000231bc7b   # regenerate
    python -m agent.vocab                                                          # report only

``--image`` reads MANIFEST.json of /models/support and /models/coding from the predictor image
(docker run, no network) and writes configs/live/predictor_vocab.json. Without it, the
committed file is read. The report lists, for every tool the LLM can call, the gateway class,
whether the profile's vocabulary knows it, and the server tools/tool_map.yaml routes it to.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
VOCAB = ROOT / "configs" / "live" / "predictor_vocab.json"
TOOL_MAP = ROOT / "tools" / "tool_map.yaml"
PROBE = ROOT / "configs" / "live" / "predictor_probe.json"
PROFILES = ("support", "coding")
# Runs INSIDE the predictor image: the served engine's answer for every prefix of a flow
# (history with 3 s gaps, 200 ms calls). Shows where the profile ranks well and where it
# abstains (mode "prior"/"none": no warm decisions).
PROBE_PY = """
import json, sys
from pathlib import Path
from predictors.cold_start import ColdStarts
from services.predictor.engine import load_bundle, read_folder, answer
from services.predictor.schema import parse_request
flows = json.loads(sys.argv[1])
cold = ColdStarts.load('/cold/cold_start.yaml')
out = {}
for prof, flow in flows.items():
    b = load_bundle(read_folder(Path('/models') / prof), cold)
    rows = []
    for k in range(len(flow) + 1):
        h = [{"tool_class": c, "t_start_ms": 3000.0 * i, "t_end_ms": 3000.0 * i + 200,
              "error": False, "batch_id": i} for i, c in enumerate(flow[:k])]
        a = answer(b, parse_request({"session_id": "probe", "now_ms": 3000.0 * k,
                                     "history": h}))
        rows.append({"after": flow[:k], "next_in_flow": flow[k] if k < len(flow) else None,
                     "mode": a["mode"], "top3": [[r["tool_class"], r["p"]] for r in
                                                 a["ranked"][:3]],
                     "warm": sorted(d["tool_class"] for d in a["decisions"] if d["warm"])})
    out[prof] = {"model_version": b.version, "rows": rows}
print(json.dumps(out))
"""
READ = ("import json\nout={}\nfor p in %r:\n m=json.load(open(f'/models/{p}/MANIFEST.json'))\n"
        " out[p]={k:m.get(k) for k in ('model_version','source','vocab_map','profile',"
        "'git_commit','cold_start_sha256','synthetic','vocabulary')}\nprint(json.dumps(out))")


def from_image(image: str) -> dict:
    out = subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint",
                          "python", image, "-c", READ % (PROFILES,)],
                         check=True, capture_output=True, text=True).stdout
    return {"image": image.rsplit("/", 1)[-1], "generated_by": "python -m agent.vocab --image",
            "profiles": json.loads(out)}


def probe(image: str) -> dict:
    from agent.tasks import get

    flows = {}
    for scen in ("airline", "codefix"):
        t = get(scen)
        flows[t.profile] = [t.gateway_call(n, {})[0] for n, _ in t.scripted(0)]
    out = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "-v", f"{ROOT / 'configs'}:/cold:ro",
         "--entrypoint", "python", image, "-c", PROBE_PY, json.dumps(flows)],
        check=True, capture_output=True, text=True).stdout
    return {"image": image.rsplit("/", 1)[-1], "generated_by": "python -m agent.vocab --image",
            "note": "history with 3 s gaps and 200 ms calls; the live gaps differ",
            "profiles": json.loads(out.strip().splitlines()[-1])}


def server_of(cls: str, tm: dict) -> str:
    for name, spec in tm["servers"].items():
        if cls in spec.get("classes", []):
            return name
    return tm["fallback"]


def coverage(vocab: dict) -> list[dict]:
    from agent.tasks import get

    tm = yaml.safe_load(TOOL_MAP.read_text())
    rows = []
    for scen in ("airline", "codefix"):
        task = get(scen)
        known = set(vocab["profiles"][task.profile]["vocabulary"])
        for tool in task.tool_names:
            cls, _ = task.gateway_call(tool, {})
            rows.append({"task": scen, "profile": task.profile, "llm_tool": tool,
                         "gateway_class": cls, "in_vocabulary": cls in known,
                         "server": server_of(cls, tm)})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", help="predictor image to read the bundle manifests from")
    a = ap.parse_args(argv)
    if a.image:
        VOCAB.parent.mkdir(parents=True, exist_ok=True)
        VOCAB.write_text(json.dumps(from_image(a.image), indent=1, sort_keys=True) + "\n")
        PROBE.write_text(json.dumps(probe(a.image), indent=1) + "\n")
        print(f"wrote {VOCAB.relative_to(ROOT)}, {PROBE.relative_to(ROOT)}")
    vocab = json.loads(VOCAB.read_text())
    print(f"predictor image {vocab['image']}")
    for p, m in vocab["profiles"].items():
        print(f"  {p}: {m['model_version']} (source {m['source']}, synthetic {m['synthetic']}): "
              f"{', '.join(v for v in m['vocabulary'] if v != '<end>')}")
    print("| task | profile | LLM tool | gateway class | in vocabulary | server |")
    print("|---|---|---|---|---|---|")
    rows = coverage(vocab)
    for r in rows:
        print(f"| {r['task']} | {r['profile']} | {r['llm_tool']} | {r['gateway_class']} | "
              f"{'yes' if r['in_vocabulary'] else 'NO'} | {r['server']} |")
    if PROBE.exists():
        pr = json.loads(PROBE.read_text())
        print(f"\nPredictor along the scripted flows ({pr['note']}):")
        print("| profile | after | next in flow | mode | top-3 | warms |")
        print("|---|---|---|---|---|---|")
        for prof, d in pr["profiles"].items():
            for r in d["rows"]:
                top = ", ".join(f"{t} {p:.2f}" for t, p in r["top3"])
                print(f"| {prof} | {' > '.join(r['after']) or '(turn start, empty)'} | "
                      f"{r['next_in_flow'] or '-'} | {r['mode']} | {top} | "
                      f"{', '.join(r['warm']) or 'nothing (abstains)'} |")
    return 0 if all(r["in_vocabulary"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
