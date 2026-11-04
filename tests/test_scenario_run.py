"""Real-timing scenario runner helpers, the session draw record, and the chart's real-predictor
values (rendered with helm when it is installed)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gateway.scenario_run import batches, history, metrics, nearest_rank

ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "charts" / "skypiea"


def test_batches_keep_recorded_gaps_and_group_parallel_calls():
    calls = [{"tool_class": "a", "t_ms": 1000, "service_ms": 50, "batch_id": 0},
             {"tool_class": "b", "t_ms": 5000, "service_ms": 200, "batch_id": 1},
             {"tool_class": "c", "t_ms": 5000, "service_ms": 100, "batch_id": 1},
             {"tool_class": "d", "t_ms": 9000, "service_ms": 10, "batch_id": 2}]
    b = batches(calls)
    assert [g for g, _ in b] == [0.0, 3950.0, 3800.0]     # gap from the END of the prior batch
    assert [[c["tool_class"] for c in grp] for _, grp in b] == [["a"], ["b", "c"], ["d"]]
    assert history(calls[:1]) == [{"tool_class": "a", "t_start_ms": 1000, "t_end_ms": 1050,
                                   "error": False, "batch_id": 0}]


def test_nearest_rank_and_metric_sum():
    xs = list(range(1, 101))
    assert nearest_rank(xs, 50) == 50 and nearest_rank(xs, 99) == 99
    assert nearest_rank([7.0], 99) == 7.0 and nearest_rank([], 50) is None
    text = "# HELP x\nfoo_total{tool=\"a\"} 2.0\nfoo_total{tool=\"b\"} 3.5\nfoo_created 9\n"
    assert metrics(text, ["foo_total", "bar"]) == {"foo_total": 5.5, "bar": 0.0}


def test_session_record_names_source_split_and_ids():
    rec = json.loads((ROOT / "results" / "scenario_sessions.json").read_text())
    for scen, src in (("A", "tau2_bench"), ("B", "tracelab")):
        s = rec["scenarios"][scen]
        assert s["source"] == src and s["split"].startswith("test")
        assert len(s["session_ids"]) == rec["n"] == len(set(s["session_ids"]))
        assert s["markov1_warmup"]["split"] == "train"
    assert max(rec["scenarios"]["B"]["duration_s"]) <= 600


def _render(*sets: str) -> subprocess.CompletedProcess:
    args = ["helm", "template", "s", str(CHART), "-f", str(CHART / "values-kind.yaml"),
            "--set", "imageTag=t"]
    for s in sets:
        args += ["--set-file" if s.startswith("platform.predictor.coldStartYaml") else "--set", s]
    return subprocess.run(args, capture_output=True, text=True, timeout=30)


@pytest.mark.skipif(not shutil.which("helm"), reason="helm not installed")
def test_chart_real_predictor_values():
    assert "skypiea-model" not in _render().stdout             # default: stub only
    assert _render("platform.predictor.serve=model").returncode != 0   # needs image.tag
    assert _render("platform.predictor.image.tag=x").returncode != 0   # needs the cold file
    cold = f"platform.predictor.coldStartYaml={ROOT / 'configs' / 'cold_start.yaml'}"
    bad = _render("platform.predictor.image.tag=x", cold, "platform.predictor.profile=nope")
    assert bad.returncode != 0
    r = _render("platform.predictor.image.tag=x", cold, "platform.predictor.profile=coding",
                "platform.predictor.serve=model")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "image: skypiea/predictor:x" in out.replace("localhost:5001/", "")
    assert '{name: PREDICTOR_PROFILE, value: "coding"}' in out
    assert "http://skypiea-model." in out and "readOnlyRootFilesystem: true" in out


def test_report_keeps_failed_calls_and_policy_errors(tmp_path):
    from gateway.scenario_report import parse

    log = tmp_path / "a.log"
    ok = {"policy": "p", "scenario": "A", "ok": True, "cold": True, "added_ms": 5.0}
    bad = {**ok, "ok": False, "error": "boom"}
    summ = {"scenario": "A", "policy": "p", "calls": 2, "ok": 1, "metrics":
            {"skypiea_gateway_prewarm_hits_total": 3.0}, "setup": {}}
    log.write_text("REAL-TIMING run: scenario A, 1 sessions\n"
                   f"ROW {json.dumps(ok)}\nROW {json.dumps(bad)}\nPOLICY {json.dumps(summ)}\n"
                   "POLICY_ERROR markov1: RuntimeError: x\n")
    calls, rows = parse([log])
    assert [c["ok"] for c in calls] == [True, False]
    assert rows[0]["prewarm_hits"] == 3.0 and rows[0]["model_version"] == "stub"
    assert rows[1] == {"dataset": "real", "scenario": "A", "policy": "markov1",
                       "error": "RuntimeError: x", "log": "a.log"}
    # rows carry the exact images; older logs get them from --images (else the report stops)
    import sys as _sys

    from gateway import scenario_report as sr
    _sys.argv = ["x", str(log), "--out", str(tmp_path / "o")]
    with pytest.raises(SystemExit):
        sr.main()
    _sys.argv += ["--images", "a.log=predictor:t,platform:p"]
    assert sr.main() == 0
    assert "predictor:t" in (tmp_path / "o_calls.csv").read_text()
    assert calls[0]["dataset"] == "real"     # old logs without the label are real-trace runs
