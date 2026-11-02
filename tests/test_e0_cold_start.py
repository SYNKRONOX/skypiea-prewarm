"""E0 phase arithmetic and the report path (summary + figure) on synthetic probe output."""

import pytest

pytest.importorskip("matplotlib")

from experiments import e0_cold_start as e0  # noqa: E402


def probe_result(cached: bool) -> dict:
    pull = 0.0 if cached else 800.0
    return {"t_scheduled": 15.0, "pull_ms": pull, "t_proc_start": 15 + pull + 300,
            "t_health_ok": 15 + pull + 300 + 900, "t_ready": 15 + pull + 1200 + 400,
            "t_first_ok": 15 + pull + 1600 + 40}


def test_phases_sum_to_total():
    for cached in (True, False):
        ph = e0.phases(probe_result(cached))
        assert ph["image_pull"] == (0.0 if cached else 800.0)
        assert ph["container_start"] == 300 and ph["app_init"] == 900
        assert ph["readiness_lag"] == 400 and ph["first_call"] == 40
        assert sum(ph[p] for p in e0.PHASES) == pytest.approx(ph["total"])


def test_missing_timestamp_gives_none():
    r = probe_result(True)
    del r["t_ready"]
    assert e0.phases(r)["total"] is None


def test_summary_and_plot(tmp_path):
    rows = []
    for tool, tier in [("user_lookup", "light"), ("run_tests", "heavy")]:
        for mode in ("cached", "uncached"):
            for i in range(5):
                ph = e0.phases(probe_result(mode == "cached"))
                rows.append({"tool": tool, "tier": tier, "mode": mode, "mode_ok": True,
                             "error": None, "warm_median_ms": 5.0 + i,
                             **{f"{k}_ms": v for k, v in ph.items()}})
    rows.append({"tool": "run_tests", "tier": "heavy", "mode": "cached", "error": "boom"})
    summary, ok = e0.summarise(rows)
    assert len(summary) == 4 and len(ok) == 20
    rt = summary[(summary.tool == "run_tests") & (summary["mode"] == "cached")].iloc[0]
    assert rt["n"] == 5 and rt["n_failed"] == 1
    e0.plot(summary, "test", tmp_path / "e0.png")
    assert (tmp_path / "e0.png").stat().st_size > 10_000
    text = "\n".join(e0.verdict(summary, ok, "test"))
    assert "dominant phase" in text and "ABOVE 1 s" in text


def test_noisy_runs_counted_and_quiet_median():
    rows = []
    for i in range(6):
        ph = e0.phases(probe_result(True))
        ph["total"] += 1000 * (i >= 4)  # the two noisy runs are slower
        rows.append({"tool": "shell", "tier": "light", "mode": "cached", "mode_ok": True,
                     "error": None, "noisy": i >= 4, "warm_median_ms": 5.0,
                     **{f"{k}_ms": v for k, v in ph.items()}})
    summary, ok = e0.summarise(rows)
    r = summary.iloc[0]
    assert r["n"] == 6 and r["n_noisy"] == 2
    assert r["total_median_quiet_ms"] < r["total_p95_ms"]
    assert "noisy runs: 2 of 6" in "\n".join(e0.verdict(summary, ok, "kind"))


def test_parallel_groups_cover_every_tool():
    order = [f"t{i}" for i in range(16)]
    groups = e0.groups_for_round(order, 3)
    assert all(len(g) == 3 and len(set(g)) == 3 for g in groups)
    assert {t for g in groups for t in g} == set(order)


def test_app_init_uses_server_startup_and_pull_includes_wait():
    r = probe_result(False)
    r.update(app_startup_ms=700.0, pull_incl_wait_ms=1800.0, t_proc_start=15 + 1800 + 300)
    r.update(t_health_ok=r["t_proc_start"] + 1400)  # probe detected the app 700 ms late
    ph = e0.phases(r)
    assert ph["app_init"] == 700 and ph["image_pull"] == 1800 and ph["container_start"] == 300
    assert sum(ph[p] for p in e0.PHASES) == pytest.approx(ph["total"])


def test_pull_time_recovered_from_kubelet_event():
    r = probe_result(False)
    del r["pull_ms"]
    r["pull_event"] = ('Successfully pulled image "x" in 1.586s (1.8s including waiting). '
                       'Image size: 63224350 bytes.')
    assert e0.pull_times(r) == (1586.0, 1800.0)
    assert e0.phases(r)["image_pull"] == 1800.0
    r2 = {"pull_event": 'Container image "x" already present on machine'}
    assert e0.pull_times(r2) == (0.0, None)
