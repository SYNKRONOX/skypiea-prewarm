"""Skypiea Live Console: a local web UI (http://127.0.0.1:8090) that runs a chat with a local
LLM, mirrors every tool call to the baseline and the predictive stack, and shows live what
the platform reports. Server-sent events (/events) carry every update; the page is plain
HTML/CSS/JS served from console/static (no CDN, no external fonts or scripts: works offline).

    python -m console            # needs demo/live_stacks.sh up + its port-forwards

The console OBSERVES: the tool calls go from the agent to the gateways, and the console
reads the controllers' state, the gateway's session record (the data behind `skypiea trace`
and `explain`) and the predictor's /healthz. Its buttons act through the platform's own
interfaces (stub /admin/policy, kubectl scale for the predictor, helm for the profile).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from agent.tasks import get as get_task
from agent.tasks.live import PROFILE_LABELS, SCRIPTS
from console import truth
from console.engine import TURN_FIELDS, Conversation, pricing, replay_plan, to_csv, totals
from console.poller import Poller
from console.stacks import BASE, BASELINE_POLICIES, PRED, StackClient, kubectl

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
EXPORTS = ROOT / ".live" / "exports"
DEMOS = {"airline": "support", "codefix": "coding", "skyair": "support"}
AGENTS = {"skyair": "Skye", "codefix": "Dev assistant", "airline": "Agent"}
DRAIN_TIMEOUT_S = 180
# Autopilot typing model (an ASSUMPTION, not a measurement): the scripted customer "types"
# each message at 200 characters per minute (about 40 words per minute) before it is sent,
# in EVERY hint mode, so cells differ only in when the hint fires. 0 = send at once.
AUTOPILOT_CPM = int(os.environ.get("CONSOLE_AUTOPILOT_CPM", "200"))
# An abandoned typing hint is charged the idle pod-seconds of the servers it warmed over this
# window after the hint (TTL 30 s + 5 s purge + 5 s dwell).
ABANDON_WINDOW_S = 40.0


def client_label(request: Any, d: dict[str, Any]) -> str:
    """Who sent a settings change: the declared client (body "client" or header
    X-Skypiea-Client: the console page sends "console page", the run scripts their name),
    else the User-Agent's first word, and the peer address."""
    who = str(d.get("client") or request.headers.get("x-skypiea-client") or "").strip()[:40]
    if not who:
        ua = request.headers.get("user-agent", "")
        who = (ua.split("/")[0] or "unknown client")[:40]
    peer = request.client.host if request.client else "?"
    return f"{who} @ {peer}"


def chat_view(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """User and assistant text per turn (for re-drawing the chat after a reload)."""
    out, turn = [], 0
    for m in messages:
        if m.get("role") == "user":
            turn += 1
        if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip():
            out.append({"role": m["role"], "text": m["content"], "turn": turn})
    return out


class NoCacheStatic(StaticFiles):
    """Static files with Cache-Control: no-cache (a browser never runs an old console UI)."""

    async def get_response(self, path, scope):
        r = await super().get_response(path, scope)
        r.headers["Cache-Control"] = "no-cache"
        return r


class Hub:
    def __init__(self) -> None:
        self.subs: set[asyncio.Queue] = set()
        self.last: dict[str, dict] = {}     # latest event per type (snapshot for new tabs)

    async def publish(self, ev: dict[str, Any]) -> None:
        ev = {**ev, "ts": round(time.time(), 3)}
        if ev["type"] in ("pods", "slow", "totals", "predictor", "settings", "status"):
            self.last[ev["type"]] = ev
        for q in list(self.subs):
            if q.qsize() < 2000:
                q.put_nowait(ev)


class Console:
    def __init__(self, llm: Any, kube_context: str = "kind-skypiea",
                 base: StackClient | None = None, pred: StackClient | None = None,
                 start_poller: bool = True, mcp_factory=None):
        self.hub = Hub()
        self.llm = llm
        self.ctx = kube_context
        self.stacks = {"base": base or StackClient(BASE), "pred": pred or StackClient(PRED)}
        self.poller = Poller(self.stacks, self.hub.publish, self.model_health)
        self.poller.on_slow = self.publish_totals
        self.start_poller = start_poller
        self.mcp_factory = mcp_factory
        # hint_mode: off | enter (hint when the message is sent) | typing (first keystroke
        # of each turn, and when a conversation opens). "hint" mirrors it for old clients.
        self.settings = {"mirror": "concurrent", "hint": False, "hint_mode": "off",
                         "baseline_policy": "scale_to_zero",
                         "demo": os.environ.get("CONSOLE_DEMO", "skyair"),
                         "autopilot": False, "autopilot_stopping": False, "busy": None,
                         "lookahead": 1, "console_map": None,
                         "autopilot_cpm": AUTOPILOT_CPM,
                         "llm_model": getattr(llm, "model", "?"), "llm_provider": "",
                         "story": None}
        self.hint_changes: list[dict[str, Any]] = []   # every hint change, with its sender
        self.conv: Conversation | None = None
        self.previous: Conversation | None = None
        self.tasks: set[asyncio.Task] = set()
        self._kube = None

    # ---------------- helpers ----------------
    def spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    async def status(self, text: str, level: str = "info") -> None:
        await self.hub.publish({"type": "status", "text": text, "level": level})

    async def model_health(self) -> dict[str, Any]:
        """The predictor's /healthz through the API server's service proxy (survives the
        predictor pod being killed and restarted; a port-forward would not)."""
        def get() -> dict[str, Any]:
            if self._kube is None:
                from skypiea_cli.backend import KubeBackend
                self._kube = KubeBackend(context=self.ctx, namespace=PRED.namespace)
            code, data = self._kube._proxy("services", "skypiea-model", 80, "/healthz")
            replicas = None
            try:
                d = self._kube.apps.read_namespaced_deployment("skypiea-model", PRED.namespace)
                replicas = {"spec": d.spec.replicas or 0, "ready": d.status.ready_replicas or 0}
            except Exception:  # noqa: BLE001
                pass
            if code != 200 or not isinstance(data, dict):
                return {"status": "unreachable", "http": code, "replicas": replicas}
            return {**data, "replicas": replicas}
        try:
            return await asyncio.to_thread(get)
        except Exception as e:  # noqa: BLE001
            return {"status": "unreachable", "error": type(e).__name__}

    def snapshot(self) -> dict[str, Any]:
        task = get_task(self.settings["demo"])
        conv = self.conv
        from agent.tasks.stories import stories
        demo = self.settings["demo"]
        return {"type": "snapshot", "settings": self.settings,
                "demo": {"name": demo, "profile": task.profile,
                         "profile_label": PROFILE_LABELS[task.profile],
                         "agent": AGENTS.get(demo, "Agent"),
                         "human": "Developer" if demo == "codefix" else "Customer",
                         "stories": [{"act": x["act"], "title": x["title"]}
                                     for x in stories(demo)],
                         "tools": task.tool_names, "script": SCRIPTS[demo]},
                "llm_model": getattr(self.llm, "model", "?"),
                "pricing": pricing(),
                "hint_changes": self.hint_changes[-10:],
                "console_map_file": "configs/live/console_tool_map.yaml",
                "conversation": None if conv is None else {
                    "id": conv.id, "turn": conv.turn, "calls": conv.calls,
                    "turns": conv.turns, "abandoned": conv.abandoned,
                    "booking": truth.state(conv.backend), "writes": conv.writes[-12:],
                    "truth": conv.truth, "chat": chat_view(conv.messages),
                    "hint_changes": conv.hint_changes,
                    "skye_state": conv.backend.state_view()
                    if hasattr(conv.backend, "state_view") else None,
                    "guards": conv.guards[-12:],
                    "totals": totals(conv.calls, conv.idle_delta(), pricing())},
                "last": self.hub.last}

    async def publish_totals(self) -> None:
        """The current conversation's totals with the idle counters just read (every 2 s).
        Without this the summary only moved when a call ended, so idle pod-seconds of pods
        kept warm during think time and after the last call were missing (it could read
        "kept pods warm 0.0 s longer" while Skypiea pods were visibly warm)."""
        conv = self.conv
        if conv is None or not conv.calls:
            return
        await self.hub.publish({"type": "totals", "conversation": conv.id,
                                "totals": totals(conv.calls, conv.idle_delta(), pricing())})

    async def publish_settings(self) -> None:
        await self.hub.publish({"type": "settings", "settings": self.settings})

    def task_name(self) -> str:
        d = self.settings["demo"]
        return "airline_cm" if d == "airline" and self.settings.get("console_map") else d

    def new_conversation(self) -> Conversation:
        if self.conv is not None:
            pend = self.conv.typing_cancelled()       # typed but never sent: abandoned
            if pend and pend["turn"] > self.conv.turn:
                self.spawn(self.measure_abandoned(self.conv, pend))
            self.previous = self.conv
            self.previous.close()     # ends its MCP sessions and its baseline lane task
        self.conv = Conversation(get_task(self.task_name()), self.llm,
                                 self.stacks, self.poller, self.hub.publish, self.settings,
                                 mcp_factory=self.mcp_factory)
        try:
            asyncio.get_running_loop()
            self.spawn(self.conv.open_hint())
        except RuntimeError:
            pass
        return self.conv

    def servers_of(self, classes: list[str]) -> list[str]:
        import yaml as _y

        from tools.common.toolmap import apply_overrides, parse
        cfg = _y.safe_load((ROOT / "tools" / "tool_map.yaml").read_text())
        tm = parse(apply_overrides(cfg, self.settings.get("console_map") or {}))
        return sorted({tm.server_for(c) or tm.fallback for c in classes})

    async def measure_abandoned(self, conv: Conversation, pend: dict[str, Any]) -> None:
        """Idle pod-seconds that an abandoned typing hint caused on the PREDICTIVE stack: the
        servers its decision warmed, from the hint until ABANDON_WINDOW_S after it (controller
        counter skypiea_pod_seconds_idle_total, read every 2 s). Other idle pods of the same
        servers in that window are included, so this is an upper bound."""
        sess = await self.stacks["pred"].session(pend["session"])
        dec = next((d for d in (sess or {}).get("decisions", [])
                    if d.get("trigger") == "turn_start" and d.get("turn") == pend["turn"]
                    and d.get("outcome") == "ok"), None)
        warmed = sorted({d["tool_class"] for d in (dec or {}).get("decisions", [])
                         if d.get("warm") and d.get("copies", 0) > 0})
        servers = self.servers_of(warmed) if warmed else []
        idle0 = {s: self.poller.idle.get("pred", {}).get(s, 0.0) for s in servers}
        wait = pend["hint_t"] + ABANDON_WINDOW_S - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        idle = sum(max(0.0, self.poller.idle.get("pred", {}).get(s, 0.0) - idle0[s])
                   for s in servers)
        row = {"conversation": conv.id, "turn": pend["turn"], "warmed": warmed,
               "servers": servers, "idle_pod_s": round(idle, 1),
               "window_s": ABANDON_WINDOW_S, "note": "upper bound (other idle pods of the "
                                                     "same servers count too)"}
        conv.abandoned.append(row)
        await self.hub.publish({"type": "abandoned", "abandoned": row})

    async def busy(self, what: str | None) -> None:
        self.settings["busy"] = what
        await self.publish_settings()

    # ---------------- actions ----------------
    async def chat(self, text: str, source: str = "presenter",
                   typing_ms: float | None = None) -> None:
        conv = self.conv or self.new_conversation()
        await self.hub.publish({"type": "conversation", "id": conv.id})
        await conv.user_turn(text, source, typing_ms)

    async def typing(self, event: str) -> None:
        conv = self.conv or self.new_conversation()
        if event == "start":
            await conv.typing_started()
        elif event == "cancel":
            pend = conv.typing_cancelled()
            if pend:
                self.spawn(self.measure_abandoned(conv, pend))

    async def autopilot(self, turns: int | None = None,
                        script: list[str] | None = None) -> None:
        """Play the prepared script as the human side (the LLM stays live). `autopilot` stays
        true until the loop has really ended; a stop request only sets `autopilot_stopping`
        (the current turn finishes first)."""
        self.settings.update(autopilot=True, autopilot_stopping=False)
        await self.publish_settings()
        try:
            cpm = self.settings.get("autopilot_cpm") or 0
            for i, msg in enumerate(script or SCRIPTS[self.settings["demo"]]):
                if self.settings["autopilot_stopping"] or (turns is not None and i >= turns):
                    break
                typing_ms = None
                if cpm > 0:      # the declared typing model (same in every hint mode)
                    await self.typing("start")
                    typing_ms = 60_000.0 * len(msg) / cpm
                    await asyncio.sleep(typing_ms / 1000.0)
                await self.chat(msg, "autopilot", typing_ms)
        finally:
            self.settings.update(autopilot=False, autopilot_stopping=False)
            await self.publish_settings()

    async def story(self, act: int) -> None:
        """Story mode: reset, then the act's prepared lines through the autopilot (LIVE
        model). An act with kill_after_turn kills the predictor after that many turns and
        restarts it when the act has ended."""
        from agent.tasks.stories import stories
        st = next(x for x in stories(self.settings["demo"]) if x["act"] == act)
        self.settings["story"] = st["title"]
        await self.publish_settings()
        try:
            await self.reset()
            k = st.get("kill_after_turn")
            if not k:
                await self.autopilot(None, st["script"])
                return
            await self.autopilot(k, st["script"])
            if self.settings.get("autopilot_stopping") or self.conv is None or \
                    self.conv.turn < k:
                return
            await self.status(f"{st['title']}: killing the predictor now (mid-conversation)")
            await self.predictor("kill")
            for _ in range(60):
                if (await self.model_health()).get("status") != "ok":
                    break
                await asyncio.sleep(1)
            try:
                await self.autopilot(None, st["script"][k:])
            finally:
                await self.status(f"{st['title']}: restarting the predictor")
                await self.predictor("restart")
        finally:
            self.settings["story"] = None
            await self.publish_settings()

    async def drain(self) -> bool:
        """Wait until both controllers report every tool at 0 replicas. Nothing is forced:
        warm pods leave when their TTL (+5 s purge) runs out."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < DRAIN_TIMEOUT_S:
            if self.poller.all_zero():
                return True
            await self.status(f"waiting for both stacks to reach 0 pods (longest TTL left "
                              f"{self.poller.max_ttl_left():.0f} s; nothing is forced)")
            await asyncio.sleep(1)
        return False

    async def reset(self) -> None:
        await self.busy("reset")
        try:
            ok = await self.drain()
            await self.poller.refresh_idle()     # exact baseline: nothing is warm now
            self.new_conversation()
            await self.hub.publish({"type": "reset", "conversation": self.conv.id,
                                    "drained": ok})
            await self.status("both stacks at 0 pods; new conversation" if ok else
                              f"not at 0 after {DRAIN_TIMEOUT_S} s; new conversation anyway",
                              "info" if ok else "warn")
        finally:
            await self.busy(None)

    async def replay(self) -> None:
        src = self.conv if (self.conv and self.conv.calls) else self.previous
        if src is None or not src.calls:
            await self.status("nothing to replay yet", "warn")
            return
        plan = replay_plan(src.calls, src.args)
        await self.busy("replay")
        try:
            await self.drain()
            conv = self.new_conversation()
            await self.hub.publish({"type": "reset", "conversation": conv.id, "drained": True,
                                    "replay_of": src.id})
            await self.status(f"replaying {len(plan)} calls of {src.id} with the recorded "
                              f"gaps (no LLM)")
            await conv.replay(plan)
        finally:
            await self.busy(None)

    async def set_hint(self, mode: str, by: str) -> None:
        """Change the turn-hint mode. Every change is kept with who sent it (W1 night: the
        hint was once switched off mid-run by a request nobody could identify), stored on the
        conversation (its report and turns CSV carry it) and, if it happens while a
        conversation is under way, shown as a warning in the chat."""
        old = self.settings.get("hint_mode") or "off"
        self.settings.update(hint_mode=mode, hint=mode != "off")
        await self.publish_settings()
        if old == mode:
            return
        conv = self.conv
        ch = {"t": round(time.time(), 3), "from": old, "to": mode, "by": by,
              "turn": conv.turn if conv else None,
              "running": bool(self.settings.get("autopilot") or self.settings.get("story")
                              or (conv is not None and conv.lock.locked()))}
        self.hint_changes.append(ch)
        del self.hint_changes[:-50]
        if conv is not None:
            conv.hint_changes.append(ch)
        mid = conv is not None and (conv.turn > 0 or ch["running"])
        await self.hub.publish({"type": "hint_change", "change": ch, "mid_conversation": mid})
        await self.status(f"turn hint {old} -> {mode} (by {by})"
                          + (" during this conversation: later turns use the new setting"
                             if mid else ""), "warn" if mid else "info")
        if mode == "typing" and conv is not None:
            self.spawn(conv.open_hint())

    async def set_policy(self, key: str) -> None:
        await self.stacks["base"].set_policy(key)
        self.settings["baseline_policy"] = key
        await self.publish_settings()
        await self.status(f"baseline policy: {key}")

    async def predictor(self, action: str) -> None:
        n = "0" if action == "kill" else "1"
        await self.busy(f"predictor {action}")
        rc = 1
        try:
            rc, out = await kubectl("-n", PRED.namespace, "scale", "deploy/skypiea-model",
                                    f"--replicas={n}", context=self.ctx)
            if action == "restart" and rc == 0:
                rc, out = await kubectl("-n", PRED.namespace, "rollout", "status",
                                        "deploy/skypiea-model", "--timeout=180s",
                                        context=self.ctx)
            await self.status(f"predictor {action}: {'ok' if rc == 0 else 'FAILED'} ({out})",
                              "info" if rc == 0 else "error")
        finally:
            await self.busy(None)
        if action == "kill" and rc == 0 and self.conv is not None and self.conv.turn == 0:
            # Killed before the conversation started: a hint sent at Reset (typing mode)
            # may already have warmed pods. Start again from 0 pods, so the "no predictor"
            # demo really begins cold (W1 P0 #4). Mid-conversation kills keep the
            # conversation; pods warmed before the kill are labelled as such per call.
            for _ in range(60):          # the pod may answer until it has terminated
                if (await self.model_health()).get("status") != "ok":
                    break
                await asyncio.sleep(1)
            await self.status("predictor down; draining pods warmed before the kill, then a "
                              "new conversation")
            await self.reset()

    async def models(self) -> list[dict[str, Any]]:
        from console.models import list_models
        http = getattr(self.llm, "http", None)
        own = http is None
        http = http or httpx.AsyncClient(timeout=5)
        try:
            return await list_models(http, getattr(self.llm, "model", None))
        finally:
            if own:
                await http.aclose()

    async def set_model(self, entry: dict[str, Any], llm: Any = None) -> None:
        """Switch the LLM; the conversation is reset (drain to 0 pods, new conversation), so
        one conversation never mixes two models. The model is in every exported row."""
        from console.models import make_llm
        await self.busy(f"switching model to {entry['model']}")
        try:
            old, self.llm = self.llm, llm or make_llm(entry)
            close = getattr(getattr(old, "http", None), "aclose", None)
            if close is not None and old is not self.llm:
                with contextlib.suppress(Exception):
                    await close()
            self.settings["llm_model"] = self.llm.model
            self.settings["llm_provider"] = entry.get("provider", "")
        finally:
            await self.busy(None)
        await self.reset()
        await self.hub.publish(self.snapshot())
        await self.status(f"model: {self.llm.model} ({entry.get('provider', '')}); new "
                          "conversation")

    async def switch_demo(self, name: str) -> None:
        profile = DEMOS[name]
        await self.busy(f"switching to {name} (profile {profile})")
        try:
            h = await self.model_health()
            if h.get("profile") != profile:
                await self.status(f"helm: predictive stack profile -> {profile} (about 30-60 s)")
                p = await asyncio.to_thread(
                    subprocess.run, [str(ROOT / "demo" / "live_stacks.sh"), "profile", profile],
                    capture_output=True, text=True, timeout=900)
                if p.returncode != 0:
                    await self.status(f"profile switch FAILED: {(p.stdout + p.stderr)[-200:]}",
                                      "error")
                    return
            self.settings["demo"] = name
            self.new_conversation()
            await self.hub.publish({"type": "reset", "conversation": self.conv.id,
                                    "drained": None})
            await self.hub.publish(self.snapshot())
            await self.status(f"demo {name}: {PROFILE_LABELS[profile]}")
        finally:
            await self.busy(None)

    async def export(self, what: str = "calls") -> tuple[str, str]:
        conv = self.conv if (self.conv and self.conv.calls) else self.previous
        EXPORTS.mkdir(parents=True, exist_ok=True)
        cid = conv.id if conv else "empty"
        if what == "turns":
            name = f"console_{time.strftime('%Y%m%dT%H%M%S')}_{cid}_turns.csv"
            text = to_csv(conv.turns if conv else [], TURN_FIELDS)
        else:
            name = f"console_{time.strftime('%Y%m%dT%H%M%S')}_{cid}.csv"
            text = to_csv(conv.calls if conv else [])
        (EXPORTS / name).write_text(text)
        return name, text

    async def detect(self) -> None:
        """What the stacks are running: predictor lookahead (model /healthz) and the console
        tool mapping (gateway /healthz class_overrides)."""
        h = await self.model_health()
        self.settings["lookahead"] = int(h.get("lookahead", 1)) if h.get("status") == "ok" \
            else self.settings.get("lookahead", 1)
        try:
            g = (await self.stacks["pred"].http.get(f"{PRED.gateway}/healthz")).json()
            self.settings["console_map"] = g.get("class_overrides") or None
        except Exception:  # noqa: BLE001
            pass

    async def set_lookahead(self, value: int) -> None:
        await self.busy(f"predictor lookahead -> {value} (helm, about 30-60 s)")
        try:
            p = await asyncio.to_thread(
                subprocess.run, [str(ROOT / "demo" / "live_stacks.sh"), "lookahead", str(value)],
                capture_output=True, text=True, timeout=900)
            if p.returncode != 0:
                await self.status(f"lookahead switch FAILED: {(p.stdout + p.stderr)[-200:]}",
                                  "error")
                return
            await self.detect()
            self.new_conversation()
            await self.hub.publish({"type": "reset", "conversation": self.conv.id,
                                    "drained": None})
            await self.status(f"predictor lookahead {self.settings['lookahead']}")
        finally:
            await self.busy(None)


def make_app(console: Console) -> Starlette:
    c = console

    async def index(_: Request) -> Response:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    async def events(request: Request) -> Response:
        q: asyncio.Queue = asyncio.Queue()
        c.hub.subs.add(q)

        async def gen():
            try:
                yield f"data: {json.dumps(c.snapshot(), default=str)}\n\n"
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), 15)
                        yield f"data: {json.dumps(ev, default=str)}\n\n"
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                    if await request.is_disconnected():
                        break
            finally:
                c.hub.subs.discard(q)
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    async def state(_: Request) -> Response:
        return JSONResponse(json.loads(json.dumps(c.snapshot(), default=str)))

    async def body(request: Request) -> dict[str, Any]:
        try:
            d = await request.json()
            return d if isinstance(d, dict) else {}
        except ValueError:
            return {}

    async def chat(request: Request) -> Response:
        d = await body(request)
        text = str(d.get("text", "")).strip()
        if not text:
            return JSONResponse({"error": "empty message"}, status_code=400)
        if c.conv is not None and c.conv.lock.locked():
            return JSONResponse({"error": "the agent is still working"}, status_code=409)
        tm = d.get("typing_ms")
        tm = float(tm) if isinstance(tm, (int, float)) and not isinstance(tm, bool) and \
            0 <= tm < 3_600_000 else None
        c.spawn(c.chat(text, "presenter", tm))
        return JSONResponse({"accepted": True}, status_code=202)

    async def autopilot(request: Request) -> Response:
        if c.settings["autopilot"]:
            c.settings["autopilot_stopping"] = True      # stops after the current turn
            await c.publish_settings()
            return JSONResponse({"autopilot": "stopping after this turn"})
        b = await body(request)
        turns, scenario = b.get("turns"), b.get("scenario")
        script = None
        if scenario is not None:
            from agent.tasks.skye import SCENARIOS
            if c.settings["demo"] != "skyair" or scenario not in SCENARIOS:
                return JSONResponse({"error": "scenario needs the skyair demo and one of "
                                     f"{sorted(SCENARIOS)}"}, status_code=400)
            script = SCENARIOS[scenario]
        if turns is not None and (not isinstance(turns, int) or isinstance(turns, bool)
                                  or turns < 1):
            return JSONResponse({"error": "turns must be a positive integer"}, status_code=400)
        c.spawn(c.autopilot(turns, script))
        return JSONResponse({"autopilot": "started", "turns": turns, "scenario": scenario},
                            status_code=202)

    async def action(request: Request) -> Response:
        name = request.path_params["name"]
        d = await body(request)
        if c.settings["busy"] and name not in ("hint", "mirror", "typing", "cpm"):
            return JSONResponse({"error": f"busy: {c.settings['busy']}"}, status_code=409)
        if name == "reset":
            c.spawn(c.reset())
        elif name == "replay":
            c.spawn(c.replay())
        elif name == "policy":
            key = d.get("key")
            if key not in BASELINE_POLICIES:
                return JSONResponse({"error": f"key: {sorted(BASELINE_POLICIES)}"}, 400)
            await c.set_policy(key)
        elif name == "hint":
            mode = d.get("mode") or ("enter" if d.get("on") else "off")
            if mode not in ("off", "enter", "typing"):
                return JSONResponse({"error": "mode: off|enter|typing"}, status_code=400)
            await c.set_hint(mode, client_label(request, d))
            if mode == "typing" and c.conv is not None:
                c.spawn(c.conv.open_hint())
        elif name == "typing":
            if d.get("event") not in ("start", "cancel"):
                return JSONResponse({"error": "event: start|cancel"}, status_code=400)
            await c.typing(d["event"])
        elif name == "lookahead":
            if d.get("value") not in (1, 2):
                return JSONResponse({"error": "value: 1|2"}, status_code=400)
            c.spawn(c.set_lookahead(d["value"]))
        elif name == "cpm":
            v = d.get("value")
            if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 2000:
                return JSONResponse({"error": "value: chars per minute, 0-2000"}, 400)
            c.settings["autopilot_cpm"] = v
            await c.publish_settings()
        elif name == "mirror":
            if d.get("mode") not in ("concurrent", "sequential"):
                return JSONResponse({"error": "mode: concurrent|sequential"}, status_code=400)
            c.settings["mirror"] = d["mode"]
            await c.publish_settings()
        elif name == "predictor":
            if d.get("action") not in ("kill", "restart"):
                return JSONResponse({"error": "action: kill|restart"}, status_code=400)
            c.spawn(c.predictor(d["action"]))
        elif name == "model":
            if c.conv is not None and c.conv.lock.locked():
                return JSONResponse({"error": "the agent is still working"}, status_code=409)
            if c.settings["autopilot"]:
                return JSONResponse({"error": "stop the autopilot first"}, status_code=409)
            ms = await c.models()
            entry = next((m for m in ms if m["id"] == d.get("id")), None)
            if entry is None or not entry["selectable"]:
                return JSONResponse({"error": "id: one of the selectable models",
                                     "models": [m["id"] for m in ms if m["selectable"]]},
                                    status_code=400)
            c.spawn(c.set_model(entry))
        elif name == "demo":
            if d.get("name") not in DEMOS:
                return JSONResponse({"error": f"name: {sorted(DEMOS)}"}, status_code=400)
            c.spawn(c.switch_demo(d["name"]))
        else:
            return JSONResponse({"error": f"unknown action {name!r}", "actions": [
                "reset", "replay", "policy", "hint", "typing", "mirror", "predictor", "demo",
                "lookahead", "cpm", "model"]}, status_code=404)
        return JSONResponse({"ok": True, "settings": c.settings}, status_code=202)

    async def report_ep(request: Request) -> Response:
        from console.bakeoff import report
        # the current conversation once it has a turn (a conversation without tool calls,
        # e.g. out of scope, is still the one to report); before that, the previous one
        conv = c.conv if (c.conv and c.conv.turn) else (c.previous or c.conv)
        if conv is None:
            return JSONResponse({"error": "no conversation"}, status_code=404)
        sc = request.query_params.get("scenario")
        return JSONResponse(json.loads(json.dumps(report(conv, sc), default=str)))

    async def models_ep(_: Request) -> Response:
        return JSONResponse({"models": await c.models(),
                             "current": getattr(c.llm, "model", None)})

    async def personas_ep(_: Request) -> Response:
        if c.settings["demo"] == "codefix":
            from agent.tasks.live import CODEFIX_TASKS
            return JSONResponse({"title": "the sortkit repository (mock)",
                                 "agent": "Dev assistant", "personas": [
                                     {"name": "Developer (you)", "note": "suggested tasks; "
                                      "tests run in a simulated test runner",
                                      "requests": CODEFIX_TASKS}]})
        from agent.skyair.tools import personas
        return JSONResponse({"title": "Skypiea Air (fictional)", "airline":
                             "Skypiea Air (fictional)", "agent": "Skye",
                             "personas": personas() + [{
                                 "name": "A new customer", "note": "not in the database: "
                                 "Skye offers to book a trip", "requests": [
                                     "Hi, I'm Nova Quill and I've never flown with you. I'd "
                                     "like to fly from Bangalore to Goa next Friday."]}]})

    async def story_ep(request: Request) -> Response:
        from agent.tasks.stories import stories
        d = await body(request)
        acts = [x["act"] for x in stories(c.settings["demo"])]
        if d.get("act") not in acts:
            return JSONResponse({"error": f"act: one of {acts}"}, status_code=400)
        if c.settings["busy"] or c.settings["autopilot"] or c.settings.get("story"):
            return JSONResponse({"error": "busy"}, status_code=409)
        if c.conv is not None and c.conv.lock.locked():
            return JSONResponse({"error": "the agent is still working"}, status_code=409)
        c.spawn(c.story(d["act"]))
        return JSONResponse({"story": "started", "act": d["act"]}, status_code=202)

    async def booking_ep(_: Request) -> Response:
        conv = c.conv
        return JSONResponse({"state": truth.state(conv.backend if conv else None),
                             "writes": conv.writes if conv else [],
                             "truth": conv.truth if conv else []})

    async def export(request: Request) -> Response:
        name, text = await c.export("turns" if request.url.path.endswith("turns.csv")
                                    else "calls")
        return PlainTextResponse(text, media_type="text/csv", headers={
            "Content-Disposition": f'attachment; filename="{name}"'})

    async def startup() -> None:
        if not c.start_poller:
            return
        c.spawn(c.poller.run())
        try:
            await c.stacks["base"].set_policy(c.settings["baseline_policy"])
        except httpx.HTTPError:
            await c.status("baseline stub not reachable: run `make live-pf`", "error")
        h = await c.model_health()
        # keep the configured demo if the predictor serves its profile, else the first match
        if DEMOS.get(c.settings["demo"]) != h.get("profile"):
            c.settings["demo"] = next((d for d, p in DEMOS.items() if p == h.get("profile")),
                                      c.settings["demo"])
        await c.detect()
        c.new_conversation()

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        await startup()
        yield
        for t in list(c.tasks):
            t.cancel()

    return Starlette(routes=[
        Route("/", index), Route("/events", events), Route("/api/state", state),
        Route("/api/personas", personas_ep), Route("/api/models", models_ep),
        Route("/api/report", report_ep), Route("/api/story", story_ep, methods=["POST"]),
        Route("/api/skyair/state", booking_ep),
        Route("/api/chat", chat, methods=["POST"]),
        Route("/api/autopilot", autopilot, methods=["POST"]),
        Route("/api/export.csv", export), Route("/api/export_turns.csv", export),
        Route("/api/{name}", action, methods=["POST"]),
        Mount("/static", NoCacheStatic(directory=STATIC)),
    ], lifespan=lifespan)


def main() -> None:
    import uvicorn

    from console.llm_stream import StreamingLLM

    os.environ.setdefault("AGENT_BASE_URL", "http://localhost:11434/v1")
    os.environ.setdefault("AGENT_MODEL", "llama3.1:8b")
    console = Console(StreamingLLM.from_env(), os.environ.get("KIND_CTX", "kind-skypiea"))
    port = int(os.environ.get("CONSOLE_PORT", "8090"))
    print(f"Skypiea Live Console: http://127.0.0.1:{port}  (LLM {console.llm.model} at "
          f"{console.llm.base_url})", flush=True)
    uvicorn.run(make_app(console), host="127.0.0.1", port=port, log_level="warning")
