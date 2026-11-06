// Skypiea Live Console client. Plain JS, no libraries, no network beyond this server.
// Every number shown comes from an event the server sent (gateway headers, controller state,
// predictor decisions); nothing is computed from guesses. User/LLM text uses textContent only.
"use strict";

const $ = (id) => document.getElementById(id);
const S = { settings: {}, demo: {}, calls: [], grid: {}, model: {}, totals: null,
            pricing: null, llmModel: "", replyEl: null, agentEl: null, pending: {} };

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}
const fmtMs = (v) => (v === null || v === undefined) ? "–" :
  (v >= 1000 ? (v / 1000).toFixed(2) + " s" : Math.round(v) + " ms");
// money: never scientific notation; tiny amounts as "<$0.00001", small ones with 5 decimals
const fmtUsd = (v) => (v === null || v === undefined) ? "–" : v === 0 ? "$0.00" :
  v < 0.00001 ? "<$0.00001" : v < 0.01 ? "$" + v.toFixed(5) : "$" + v.toFixed(2);
const fmtN = (v, d = 0) => (v === null || v === undefined) ? "–" : Number(v).toFixed(d);

async function post(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "content-type": "application/json" },
                                body: JSON.stringify(body || {}) });
  if (!r.ok && r.status !== 202) {
    const t = await r.json().catch(() => ({}));
    status(t.error || ("HTTP " + r.status), "warn");
  }
}
function status(text, level) { const s = $("status"); s.textContent = text;
  s.className = "status " + (level || ""); }

// ---------------- header / settings ----------------
function applySettings(st) {
  S.settings = st;
  $("b-mirror").textContent = "mirror: " + st.mirror;
  $("btn-mirror").textContent = "Mirror: " + st.mirror;
  const hm = st.hint_mode || (st.hint ? "enter" : "off");
  $("b-hint").textContent = "turn hint: " + {off: "off", enter: "at Enter", typing: "at typing start"}[hm];
  $("b-hint").classList.toggle("on", hm !== "off");
  $("hintmode").value = hm;
  $("b-la").textContent = "lookahead " + (st.lookahead || 1);
  $("b-la").classList.toggle("on", (st.lookahead || 1) === 2);
  $("btn-la").textContent = "Lookahead: " + (st.lookahead || 1);
  $("btn-la").classList.toggle("on", (st.lookahead || 1) === 2);
  $("btn-la").disabled = !!st.busy;
  const cm = st.console_map;
  $("b-map").hidden = !cm;
  if (cm) $("b-map").textContent = "console mapping: " + Object.entries(cm).map(([k, v]) => k + "→" + v).join(", ");
  $("policy").value = st.baseline_policy;
  $("base-title").textContent = "(baseline: " + (st.baseline_policy === "keep_alive_30" ?
    "keep-alive 30 s" : "scale-to-zero") + ")";
  $("b-auto").hidden = !st.autopilot;
  $("b-auto").textContent = "AUTOPILOT" + (st.autopilot_cpm ? " (typing model " + st.autopilot_cpm + " chars/min, assumption)" : "");
  $("btn-auto").textContent = st.autopilot ? (st.autopilot_stopping ? "Stopping after this turn…" :
    "Stop autopilot ■") : "Autopilot ▶";
  $("b-busy").hidden = !st.busy; $("b-busy").textContent = st.busy || "";
  for (const b of ["btn-reset", "btn-replay", "btn-kill", "btn-restart"]) $(b).disabled = !!st.busy;
  $("demo").value = st.demo;
}

function applyDemo(d) {
  S.demo = d;
  $("b-profile").textContent = d.profile_label;
  footer();
}

function footer() {
  const f = $("footer"); f.textContent = "";
  const m = S.model || {};
  const parts = [
    "Local kind cluster on a laptop (cold starts are lower than on AWS; one node shared by both stacks).",
    "LLM: " + (S.llmModel || "?") + " (local, temperature 0).",
    "Predictor: " + (S.demo.profile_label || "?") + " — " + (m.model_version || "not reachable") + ".",
    "Mock tool servers. Demo runs, not results.",
  ];
  if (S.pricing) parts.push("Cost = idle pod-s × CPU request × $" + S.pricing.vcpu_second_usd +
    "/vCPU-s (" + S.pricing.status + ": " + S.pricing.source + ").");
  f.textContent = parts.join("  ");
  const lg = el("div", "legend");
  for (const [c, t] of [["--warm", "warm (pod ready on arrival)"], ["--cold", "cold (waited for a start)"],
                        ["--start", "starting"], ["--hint", "warmed by turn-start hint"]]) {
    const s = el("span"); const sw = el("span", "sw"); sw.style.background = `var(${c})`;
    s.append(sw, t); lg.append(s);
  }
  f.append(lg);
}

// ---------------- chat ----------------
function addMsg(kind, text, who, extraCls) {
  const m = el("div", "msg " + kind + (extraCls ? " " + extraCls : ""));
  m.append(el("span", "who", who), document.createTextNode(text || ""));
  $("chat").append(m); $("chat").scrollTop = 1e9; return m;
}

function thinkSvg(think, cold) {
  const W = 300, max = Math.max(think || 0, cold || 0, 1);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} 34`); svg.setAttribute("preserveAspectRatio", "none");
  const bar = (y, v, color) => { const r = document.createElementNS(svg.namespaceURI, "rect");
    r.setAttribute("x", 0); r.setAttribute("y", y); r.setAttribute("height", 13);
    r.setAttribute("width", Math.max(1, W * (v || 0) / max)); r.setAttribute("fill", color);
    svg.append(r); };
  bar(1, think, "var(--think)"); if (cold !== null) bar(19, cold, "var(--cold)");
  return svg;
}

function thinkWindow(c) {
  const box = el("div", "think");
  if (c.pos_in_reply > 0) {
    box.textContent = "Batched: sent in the same LLM reply as the previous call (think gap 0). " +
      "Prediction cannot help between calls sent in the same reply.";
    return box;
  }
  const cold = c.base_cold ? c.base_added_ms : null;
  let txt = "LLM think " + fmtMs(c.think_ms) + " vs baseline cold start for this call: ";
  if (cold === null) txt += "not measurable (the baseline call found a warm pod).";
  else if (c.think_ms >= cold) txt += fmtMs(cold) + " → the think time could hide a start begun at the previous call.";
  else txt += fmtMs(cold) + " → too short to hide it.";
  box.append(document.createTextNode(txt));
  box.append(thinkSvg(c.think_ms, cold));
  return box;
}

function callTags(c, k) {
  const t = el("div", "tags");
  const add = (txt, cls) => t.append(el("span", "tag " + (cls || ""), txt));
  if (!c[k + "_ok"]) add("FAILED " + (c[k + "_error"] || ""), "bad");
  add(c[k + "_cold"] ? "cold" : "warm", c[k + "_cold"] ? "cold" : "warm");
  add("added " + fmtMs(c[k + "_added_ms"]));
  if (c[k + "_gap_ms"] !== null && c[k + "_gap_ms"] !== undefined) add("gap " + fmtMs(c[k + "_gap_ms"]));
  if (k === "base" && c.base_gap_ms !== null && c.pred_gap_ms !== null &&
      c.base_gap_ms - c.pred_gap_ms > 100)
    add("gap +" + fmtMs(c.base_gap_ms - c.pred_gap_ms) + " vs Skypiea (waited for the LLM)", "bad");
  if (c[k + "_held_ms"]) add("held " + fmtMs(c[k + "_held_ms"]));
  if (c[k + "_predicted"]) add(c[k + "_warm_source"] === "hint" ? "warmed by hint" : "predicted",
                               c[k + "_warm_source"] === "hint" ? "hint" : "");
  const ms = c[k + "_contention_max_starting"];
  if (ms !== null && ms !== undefined)
    add("node: " + ms + " starting (n=" + c[k + "_contention_samples"] + " samples)");
  return t;
}

function onCall(c) {
  S.calls.push(c);
  delete S.pending[c.reply + ":" + c.pos_in_reply];
  // chat: the call inside its reply group
  let grp = S.replyEl;
  if (!grp || grp.dataset.reply !== String(c.reply)) {
    grp = el("div", "reply"); grp.dataset.reply = c.reply;
    grp.append(el("div", "note", "LLM reply " + c.reply + (c.replay ? " (replay)" : "")));
    $("chat").append(grp); S.replyEl = grp;
  }
  const row = el("div", "call");
  row.append(el("span", "tool", c.tool + (c.tool_class && c.tool_class !== c.tool ? " → " + c.tool_class : "")));
  row.append(el("span", "", c.results_match === false ? "RESULTS DIFFER" :
                            ("base " + fmtMs(c.base_wall_ms) + " · skypiea " + fmtMs(c.pred_wall_ms))));
  row.append(thinkWindow(c));
  if (c.tool_call_from_text) row.append(el("div", "think",
    "The model wrote this call as JSON text instead of a tool call; the console recovered it."));
  grp.append(row);
  const n = grp.querySelectorAll(".call").length;
  grp.querySelector(".note").textContent = "LLM reply " + c.reply + (c.replay ? " (replay)" : "") +
    (n > 1 ? " — batch of " + n + " calls: prediction cannot help between calls sent in the same reply" : "");
  $("chat").scrollTop = 1e9;
  renderTimelines();
}

function renderTimelines() {
  const max = Math.max(1000, ...S.calls.flatMap((c) => [c.base_wall_ms || 0, c.pred_wall_ms || 0]));
  for (const k of ["base", "pred"]) {
    const tl = $("tl-" + k); tl.textContent = "";
    for (const c of S.calls) {
      if (!c.tool_class) continue;
      const r = el("div", "trow");
      r.append(el("span", "lbl", "t" + c.turn + " r" + c.reply + "." + c.pos_in_reply + " " + c.tool));
      const bar = el("div", "bar " + (c[k + "_cold"] ? "cold" : "warm"));
      bar.style.width = (100 * (c[k + "_wall_ms"] || 0) / max) + "%";
      const w = el("div", "wait"); w.style.width = "100%"; bar.append(w);
      bar.title = "wall " + fmtMs(c[k + "_wall_ms"]) + ", gateway added " + fmtMs(c[k + "_added_ms"]);
      r.append(bar, el("span", "ms", fmtMs(c[k + "_wall_ms"])), callTags(c, k));
      tl.append(r);
    }
    tl.scrollTop = 1e9;
  }
}

function tile(k, v, sub) { const t = el("div", "tile"); t.append(el("div", "v", v), el("div", "k", sub)); return t; }

function renderTotals(T) {
  S.totals = T;
  for (const k of ["base", "pred"]) {
    const x = (T || {})[k]; const box = $("tiles-" + k); box.textContent = "";
    if (!x) continue;
    box.append(tile(k, fmtMs(x.wait_ms), "agent waited on tools (n=" + x.ok + ")"),
               tile(k, x.cold + " / " + x.ok, "cold calls"),
               tile(k, fmtMs(x.added_p50_ms), "added p50 (n=" + x.n_added + ")"),
               tile(k, fmtMs(x.added_p95_ms), "added p95 (n=" + x.n_added + ")"),
               tile(k, fmtN(x.idle_pod_s, 1) + " s", "idle pod-seconds"),
               tile(k, fmtUsd(x.idle_cost_usd || 0), "est. idle cost (assumption)"));
  }
}

function renderGrid(grid) {
  S.grid = grid;
  for (const k of ["base", "pred"]) {
    const g = $("grid-" + k); g.textContent = "";
    for (const [name, c] of Object.entries(grid[k] || {})) {
      const cell = el("div", "cell " + c.state);
      cell.title = name + ": " + c.state + ", replicas " + c.replicas + ", ready " + c.ready +
        (c.queued ? ", queued " + c.queued : "") + ", TTL left " + c.ttl_left_s + " s";
      cell.append(el("b", "", name));
      cell.append(document.createTextNode(c.state + " " + c.ready + "/" + c.replicas +
        (c.state === "warm" ? " · " + fmtN(c.warm_for_s, 1) + " s" : "")));
      g.append(cell);
    }
  }
}

function renderPredictor(decs) {
  const box = $("predictor"); box.textContent = "";
  for (const d of [...decs].reverse()) {
    const r = el("div", "dec");
    const head = d.trigger === "turn_start" ? "turn-start hint (turn " + d.turn + ")" :
      "after call " + (d.after_call + 1);
    r.append(el("b", "", head + ": "));
    if (d.outcome !== "ok") { r.append(el("span", "no", d.outcome)); box.append(r); continue; }
    r.append(document.createTextNode("top-3 " + (d.top3 || []).map((x) => x.tool_class + " " +
      Number(x.p).toFixed(2)).join(", ") + " · warmed: " + ((d.warmed || []).join(", ") || "nothing (abstained)")));
    const nx = el("div", "");
    if (d.next_tool) {
      nx.append(document.createTextNode("next call: " + d.next_tool + " — top-1 "));
      nx.append(el("span", d.top1_right ? "ok" : "no", d.top1_right ? "right" : "wrong"));
      nx.append(document.createTextNode(", warmed "));
      nx.append(el("span", d.warmed_right ? "ok" : "no", d.warmed_right ? "yes" : "no"));
    } else nx.append(document.createTextNode("next call: not yet"));
    r.append(nx); box.append(r);
  }
}

function onModel(m) {
  S.model = m || {};
  const ok = m && m.status === "ok";
  $("pred-health").textContent = ok ? "(" + m.model_version + ", profile " + m.profile + ", up)" :
    "(DOWN: " + ((m && m.status) || "?") + " — the gateway falls back to scale-to-zero)";
  $("pred-health").className = ok ? "" : "no";
  footer();
}

function renderTurn(t) {
  const r = el("div", "dec");
  r.append(el("b", "", "turn " + t.turn + " (" + t.source + "): "));
  const lead = t.lead_ms === null || t.lead_ms === undefined ? "no hint before Enter" :
    "hint " + fmtMs(t.lead_ms) + " before Enter (" + t.hint_sources + ")";
  r.append(document.createTextNode(lead + (t.typing_ms ? " · typing " + fmtMs(t.typing_ms) +
    (t.source === "autopilot" ? " (typing model)" : "") : "")));
  if (t.first_tool) {
    const d = el("div", "");
    d.append(document.createTextNode("first call " + t.first_tool + ": Skypiea " +
      fmtMs(t.pred_first_wall_ms) + " "));
    d.append(el("span", t.pred_first_warm ? "ok" : "no", t.pred_first_warm ? "warm" : "cold"));
    if (t.pred_first_predicted) d.append(document.createTextNode(" (predicted" +
      (t.pred_first_warm_source === "hint" ? " by the hint" : "") + ")"));
    d.append(document.createTextNode(" · baseline " + fmtMs(t.base_first_wall_ms) + " " +
      (t.base_first_cold ? "cold" : "warm")));
    r.append(d);
  }
  $("turns").prepend(r);
}

function renderAbandoned(a) {
  const r = el("div", "dec");
  r.append(el("b", "no", "abandoned turn " + a.turn + ": "));
  r.append(document.createTextNode("typing hint warmed " + (a.servers.join(", ") || "nothing") +
    " · idle " + a.idle_pod_s + " pod-s over " + a.window_s + " s (" + a.note + ")"));
  $("turns").prepend(r);
}

function resetView(convId) {
  S.calls = []; S.replyEl = null; S.agentEl = null;
  $("chat").textContent = ""; $("predictor").textContent = ""; $("turns").textContent = "";
  S.typing = false; S.typingT = null;
  renderTimelines(); renderTotals(null);
  $("conv-id").textContent = convId ? "(" + convId + ")" : "";
}

// ---------------- events ----------------
function handle(ev) {
  switch (ev.type) {
    case "snapshot":
      S.llmModel = ev.llm_model; S.pricing = ev.pricing;
      applySettings(ev.settings); applyDemo(ev.demo);
      resetView(ev.conversation && ev.conversation.id);
      for (const c of (ev.conversation ? ev.conversation.calls : [])) onCall(c);
      for (const t of (ev.conversation ? ev.conversation.turns || [] : [])) renderTurn(t);
      for (const a of (ev.conversation ? ev.conversation.abandoned || [] : [])) renderAbandoned(a);
      if (ev.conversation) renderTotals(ev.conversation.totals);
      for (const e of Object.values(ev.last || {})) handle(e);
      break;
    case "settings": applySettings(ev.settings); break;
    case "status": status(ev.text, ev.level); break;
    case "pods": renderGrid(ev.grid); break;
    case "slow":
      onModel(ev.model);
      if (ev.links && Object.values(ev.links).some((v) => v !== "ok"))
        status("connection: baseline gateway " + ev.links.base + ", Skypiea gateway " +
               ev.links.pred + " (port-forwards restart by themselves; else run make live-pf)", "warn");
      break;
    case "totals": renderTotals(ev.totals); break;
    case "predictor": renderPredictor(ev.decisions || []); break;
    case "reset": case "conversation":
      if (ev.type === "reset") resetView(ev.conversation);
      else $("conv-id").textContent = "(" + ev.id + ")";
      break;
    case "turn_start":
      S.agentEl = null; S.replyEl = null;
      addMsg("user", ev.text, (ev.source === "autopilot" ? "AUTOPILOT customer" :
             ev.source === "replay" ? "replay" : "presenter") + " · turn " + ev.turn,
             ev.source === "autopilot" ? "autopilot" : "");
      break;
    case "hint":
      addMsg("agent", "turn hint (" + (ev.source || "enter") + ") sent to the predictive gateway (HTTP " +
             ev.status + "): metadata only, never the text", "hint · turn " + ev.turn);
      break;
    case "turn": renderTurn(ev.turn_record); break;
    case "abandoned": renderAbandoned(ev.abandoned); break;
    case "llm_start": status("LLM thinking…"); break;
    case "llm_delta":
      if (!S.agentEl) S.agentEl = addMsg("agent", "", "agent (" + S.llmModel + ")");
      S.agentEl.append(document.createTextNode(ev.text)); $("chat").scrollTop = 1e9; break;
    case "llm_reply":
      status("LLM reply in " + fmtMs(ev.think_ms) + (ev.n_calls ? ": " + ev.n_calls + " tool call(s)" : ""));
      if (S.agentEl && ev.n_calls) S.agentEl = null;
      if (!ev.n_calls) S.agentEl = null;
      break;
    case "call_start": status("calling " + ev.tool + " on both stacks…"); break;
    case "call": onCall(ev.call); break;
    case "status_turn": status(ev.text); break;
    case "turn_end": status("your turn"); break;
    case "error": status(ev.where + ": " + ev.detail, "error"); break;
  }
}

function connect() {
  const es = new EventSource("/events");
  es.onmessage = (m) => { try { handle(JSON.parse(m.data)); } catch (e) { console.error(e); } };
  es.onerror = () => status("connection lost — retrying…", "error");
}

// ---------------- controls ----------------
$("chat-form").addEventListener("submit", (e) => {
  e.preventDefault(); const t = $("chat-input").value.trim(); if (!t) return;
  const typing_ms = S.typingT ? performance.now() - S.typingT : null;
  $("chat-input").value = ""; S.typing = false; S.typingT = null;
  post("/api/chat", { text: t, typing_ms: typing_ms });
});
// First keystroke of a turn: one metadata-only "typing start" (debounced: once per turn);
// emptying the box again counts as abandoning the turn.
$("chat-input").addEventListener("input", (e) => {
  const v = e.target.value;
  if (v.length > 0 && !S.typing) { S.typing = true; S.typingT = performance.now();
    post("/api/typing", { event: "start" }); }
  else if (v.length === 0 && S.typing) { S.typing = false; S.typingT = null;
    post("/api/typing", { event: "cancel" }); }
});
$("chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("chat-form").requestSubmit(); }
});
$("btn-auto").onclick = () => post("/api/autopilot");
$("btn-reset").onclick = () => post("/api/reset");
$("btn-replay").onclick = () => post("/api/replay");
$("hintmode").onchange = (e) => post("/api/hint", { mode: e.target.value });
$("btn-la").onclick = () => post("/api/lookahead", { value: (S.settings.lookahead || 1) === 2 ? 1 : 2 });
$("btn-mirror").onclick = () => post("/api/mirror",
  { mode: S.settings.mirror === "concurrent" ? "sequential" : "concurrent" });
$("btn-kill").onclick = () => post("/api/predictor", { action: "kill" });
$("btn-restart").onclick = () => post("/api/predictor", { action: "restart" });
$("policy").onchange = (e) => post("/api/policy", { key: e.target.value });
$("demo").onchange = (e) => post("/api/demo", { name: e.target.value });
$("btn-theme").onclick = () => {
  const r = document.documentElement;
  const dark = r.classList.contains("dark") ||
    (!r.classList.contains("light") && matchMedia("(prefers-color-scheme: dark)").matches);
  r.classList.toggle("dark", !dark); r.classList.toggle("light", dark);
};
// ?theme=light|dark forces a theme (screenshots); otherwise the system setting / ◐ button
{ const th = new URLSearchParams(location.search).get("theme");
  if (th === "light" || th === "dark") document.documentElement.classList.add(th); }
// ?snapshot=1: render the current state once (no live stream), e.g. for screenshots/printing
if (new URLSearchParams(location.search).has("snapshot")) {
  fetch("/api/state").then((r) => r.json()).then((st) => {
    handle(st);
    for (const e of Object.values(st.last || {})) handle(e);
    status("snapshot of " + new Date().toLocaleString() + " (not live)");
  });
} else {
  connect();
}
