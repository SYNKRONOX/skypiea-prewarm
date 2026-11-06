"""Drive the Live Console through its own HTTP API, like a presenter, and save what it showed.

    python -m demo.console_run --name airline_hint_off --demo airline --hint off
    python -m demo.console_run --name codefix_hint_on --demo codefix --hint on [--mirror sequential]

Steps: switch demo if needed, reset (wait until both stacks are at 0 pods), set hint and mirror
mode, play the autopilot script (the LLM is live), wait for the end, then save
<out>/<name>/events.jsonl (every server-sent event except the 4 Hz pod-grid stream, which is
summarised), export.csv (the console's CSV export) and summary.json (the console's own totals
and the per-call rows). DEMO RUNS, NOT RESULTS: one conversation each, live LLM, local kind.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path

import httpx

URL = "http://127.0.0.1:8090"


class Listener(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.events: list[dict] = []
        self.pods = 0
        self.stop = False

    def run(self) -> None:
        with httpx.stream("GET", f"{URL}/events", timeout=None) as r:
            for ln in r.iter_lines():
                if self.stop:
                    return
                if not ln.startswith("data: "):
                    continue
                ev = json.loads(ln[6:])
                if ev["type"] == "pods":
                    self.pods += 1
                    continue
                self.events.append(ev)

    def last(self, typ: str) -> dict | None:
        return next((e for e in reversed(self.events) if e["type"] == typ), None)


def wait(cond, timeout_s: float, what: str) -> None:
    t = time.monotonic()
    while not cond():
        if time.monotonic() - t > timeout_s:
            raise SystemExit(f"timeout waiting for {what}")
        time.sleep(0.5)


def settings(c: httpx.Client) -> dict:
    return c.get(f"{URL}/api/state").json()["settings"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True)
    ap.add_argument("--demo", choices=["airline", "codefix"], default="airline")
    ap.add_argument("--hint", choices=["on", "off"], default="off")
    ap.add_argument("--mirror", choices=["concurrent", "sequential"], default="concurrent")
    ap.add_argument("--policy", choices=["scale_to_zero", "keep_alive_30"],
                    default="scale_to_zero")
    ap.add_argument("--kill-predictor", action="store_true",
                    help="kill the predictor before the conversation, restart it after; every "
                         "call must still succeed (fallback = scale-to-zero)")
    ap.add_argument("--turns", type=int, default=0, help="stop autopilot after N turns")
    ap.add_argument("--out", type=Path, default=Path(".live/runs"))
    a = ap.parse_args(argv)
    out = a.out / a.name
    out.mkdir(parents=True, exist_ok=True)
    lis = Listener()
    lis.start()
    with httpx.Client(timeout=30) as c:
        if settings(c)["demo"] != a.demo:
            c.post(f"{URL}/api/demo", json={"name": a.demo})
            time.sleep(1)
            wait(lambda: not settings(c)["busy"] and settings(c)["demo"] == a.demo, 900,
                 "demo switch")
        c.post(f"{URL}/api/policy", json={"key": a.policy})
        c.post(f"{URL}/api/hint", json={"on": a.hint == "on"})
        c.post(f"{URL}/api/mirror", json={"mode": a.mirror})
        n_reset = len([e for e in lis.events if e["type"] == "reset"])
        c.post(f"{URL}/api/reset")
        wait(lambda: len([e for e in lis.events if e["type"] == "reset"]) > n_reset, 300,
             "reset")
        if a.kill_predictor:
            c.post(f"{URL}/api/predictor", json={"action": "kill"})
            time.sleep(1)
            wait(lambda: not settings(c)["busy"], 300, "predictor kill")
            wait(lambda: (lis.last("slow") or {}).get("model", {}).get("status") != "ok", 60,
                 "predictor down")
        model_before = (lis.last("slow") or {}).get("model", {})
        t0 = time.time()
        c.post(f"{URL}/api/autopilot")
        wait(lambda: settings(c)["autopilot"], 30, "autopilot start")
        if a.turns:
            wait(lambda: len([e for e in lis.events if e["type"] == "turn_end"
                              and e.get("ts", 0) >= t0]) >= a.turns, 1800, "turns")
            c.post(f"{URL}/api/autopilot")          # stop after the current turn
        wait(lambda: not settings(c)["autopilot"], 1800, "autopilot end")
        time.sleep(3)                         # last predictor refresh / totals
        state = c.get(f"{URL}/api/state").json()
        csv_text = c.get(f"{URL}/api/export.csv").text
        if a.kill_predictor:
            c.post(f"{URL}/api/predictor", json={"action": "restart"})
            time.sleep(1)
            wait(lambda: not settings(c)["busy"], 300, "predictor restart")
    lis.stop = True
    evs = [e for e in lis.events if e.get("ts", 0) >= t0 - 1]
    (out / "events.jsonl").write_text("".join(json.dumps(e, default=str) + "\n" for e in evs))
    (out / "export.csv").write_text(csv_text)
    conv = state["conversation"] or {}
    summary = {"name": a.name, "demo": a.demo, "hint": a.hint, "mirror": a.mirror,
               "baseline_policy": a.policy, "llm_model": state["llm_model"],
               "profile_label": state["demo"]["profile_label"],
               "model": (state["last"].get("slow") or {}).get("model", {}).get("model_version"),
               "conversation": conv.get("id"), "totals": conv.get("totals"),
               "pod_events_seen": lis.pods, "label": "demo run, not a result",
               "predictor_killed": a.kill_predictor,
               "predictor_status_during_run": model_before.get("status"),
               "calls": [{k: v for k, v in x.items() if not k.endswith("_error")}
                         for x in conv.get("calls", [])]}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str) + "\n")
    print(f"saved {out}: {len(summary['calls'])} calls")
    for x in summary["calls"]:
        print(f"  t{x['turn']} r{x['reply']}.{x['pos_in_reply']} {x['tool']:<18} think "
              f"{x['think_ms']:>7} | base {x.get('base_wall_ms')} cold={x.get('base_cold')} | "
              f"pred {x.get('pred_wall_ms')} cold={x.get('pred_cold')} "
              f"pred={x.get('pred_predicted')} src={x.get('pred_warm_source')}"
              f"{' FROM-TEXT' if x.get('tool_call_from_text') else ''}")
    print(json.dumps(summary["totals"], indent=1))
    if a.kill_predictor:
        # a call may fail on BOTH stacks because the LLM sent bad arguments; the check is that
        # the predictive stack (predictor dead) never fails where the baseline succeeded
        calls = [x for x in summary["calls"] if x.get("tool_class")]
        bad = [x["tool"] for x in calls if x.get("base_ok") and not x.get("pred_ok")]
        both = [x["tool"] for x in calls if not x.get("base_ok") and not x.get("pred_ok")]
        print(f"predictor killed (status during run: {summary['predictor_status_during_run']}): "
              f"{len(calls)} calls; " + ("PASS: no call failed on the predictive stack that "
                                         "succeeded on the baseline" if not bad else
                                         f"FAIL: {bad}") +
              (f"; failed on both stacks (tool argument errors): {both}" if both else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
