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
from console.engine import TURN_FIELDS, Conversation, pricing, replay_plan, to_csv, totals
from console.poller import Poller
from console.stacks import BASE, BASELINE_POLICIES, PRED, StackClient, kubectl

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
EXPORTS = ROOT / ".live" / "exports"
DEMOS = {"airline": "support", "codefix": "coding"}
DRAIN_TIMEOUT_S = 180
# Autopilot typing model (an ASSUMPTION, not a measurement): the scripted customer "types"
# each message at 200 characters per minute (about 40 words per minute) before it is sent,
# in EVERY hint mode, so cells differ only in when the hint fires. 0 = send at once.
AUTOPILOT_CPM = int(os.environ.get("CONSOLE_AUTOPILOT_CPM", "200"))
# An abandoned typing hint is charged the idle pod-seconds of the servers it warmed over this
# window after the hint (TTL 30 s + 5 s purge + 5 s dwell).
ABANDON_WINDOW_S = 40.0


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
        self.start_poller = start_poller
        self.mcp_factory = mcp_factory
        # hint_mode: off | enter (hint when the message is sent) | typing (first keystroke
        # of each turn, and when a conversation opens). "hint" mirrors it for old clients.
        self.settings = {"mirror": "concurrent", "hint": False, "hint_mode": "off",
                         "baseline_policy": "scale_to_zero", "demo": "airline",
                         "autopilot": False, "autopilot_stopping": False, "busy": None,
                         "lookahead": 1, "console_map": None,
                         "autopilot_cpm": AUTOPILOT_CPM}
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
        return {"type": "snapshot", "settings": self.settings,
                "demo": {"name": self.settings["demo"], "profile": task.profile,
                         "profile_label": PROFILE_LABELS[task.profile],
                         "tools": task.tool_names, "script": SCRIPTS[self.settings["demo"]]},
                "llm_model": getattr(self.llm, "model", "?"),
                "pricing": pricing(),
                "console_map_file": "configs/live/console_tool_map.yaml",
                "conversation": None if conv is None else {
                    "id": conv.id, "turn": conv.turn, "calls": conv.calls,
                    "turns": conv.turns, "abandoned": conv.abandoned,
                    "totals": totals(conv.calls, conv.idle_delta(), pricing())},
                "last": self.hub.last}

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

    async def autopilot(self, turns: int | None = None) -> None:
        """Play the prepared script as the human side (the LLM stays live). `autopilot` stays
        true until the loop has really ended; a stop request only sets `autopilot_stopping`
        (the current turn finishes first)."""
        self.settings.update(autopilot=True, autopilot_stopping=False)
        await self.publish_settings()
        try:
            cpm = self.settings.get("autopilot_cpm") or 0
            for i, msg in enumerate(SCRIPTS[self.settings["demo"]]):
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

    async def set_policy(self, key: str) -> None:
        await self.stacks["base"].set_policy(key)
        self.settings["baseline_policy"] = key
        await self.publish_settings()
        await self.status(f"baseline policy: {key}")

    async def predictor(self, action: str) -> None:
        n = "0" if action == "kill" else "1"
        await self.busy(f"predictor {action}")
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
        turns = (await body(request)).get("turns")
        if turns is not None and (not isinstance(turns, int) or isinstance(turns, bool)
                                  or turns < 1):
            return JSONResponse({"error": "turns must be a positive integer"}, status_code=400)
        c.spawn(c.autopilot(turns))
        return JSONResponse({"autopilot": "started", "turns": turns}, status_code=202)

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
            c.settings.update(hint_mode=mode, hint=mode != "off")
            await c.publish_settings()
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
        elif name == "demo":
            if d.get("name") not in DEMOS:
                return JSONResponse({"error": f"name: {sorted(DEMOS)}"}, status_code=400)
            c.spawn(c.switch_demo(d["name"]))
        else:
            return JSONResponse({"error": "unknown action"}, status_code=404)
        return JSONResponse({"ok": True, "settings": c.settings}, status_code=202)

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
        for demo, prof in DEMOS.items():
            if h.get("profile") == prof:
                c.settings["demo"] = demo
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
