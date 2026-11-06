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
  $("b-auto").textContent = "Autopilot"; $("b-auto").title = st.autopilot_cpm ? "typing model " + st.autopilot_cpm + " chars/min (assumption)" : "";
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
  const m = S.model || {};
  $("footer").textContent = "Local laptop cluster (kind; cold starts are lower than on AWS) · LLM " +
    (S.llmModel || "?") + " (local) · Predictor: " + (S.demo.profile_label || "?") + ", " +
    (m.model_version || "not reachable") + " · Demo runs, not results.";
}

// ---------------- plain words ----------------
const PLAIN = { find_customer: "Find customer", verify_customer: "Verify customer",
  get_customer_details: "Customer details", get_booking: "Get booking",
  search_flights: "Search flights", change_flight: "Change flight", cancel_booking: "Cancel booking",
  add_baggage: "Add baggage", issue_refund: "Issue refund", transfer_to_human: "Transfer to a human",
  user_lookup: "Look up customer", reservation_lookup: "Get booking", flight_search: "Search flights",
  reservation_modify: "Change booking", reservation_cancel: "Cancel booking", escalate: "Transfer to a human" };
const plain = (t) => PLAIN[t] || String(t || "?").replace(/_/g, " ").replace(/^./, (x) => x.toUpperCase());
const secs = (ms) => (ms / 1000).toFixed(2) + " s";

// ---------------- chat ----------------
function chatEmpty(show) { const e = $("chat-empty"); if (e) e.hidden = !show; }
function addMsg(kind, text, who) {
  chatEmpty(false); dropTyping();
  const m = el("div", "msg " + kind);
  if (who) m.append(el("span", "who", who));
  m.append(document.createTextNode(text || ""));
  $("chat").append(m); $("chat").scrollTop = 1e9; return m;
}
function pill(text, cls) {
  chatEmpty(false);
  let row = $("chat").lastElementChild;
  if (!row || !row.classList.contains("pills")) { row = el("div", "pills"); $("chat").append(row); }
  const p = el("span", "pill " + (cls || ""), text); row.append(p); $("chat").scrollTop = 1e9; return p;
}
function showTyping() { if (S.typingEl) return; chatEmpty(false);
  S.typingEl = el("div", "typing"); S.typingEl.setAttribute("aria-label", "Skye is typing");
  S.typingEl.append(el("i"), el("i"), el("i")); $("chat").append(S.typingEl); $("chat").scrollTop = 1e9; }
function dropTyping() { if (S.typingEl) { S.typingEl.remove(); S.typingEl = null; } }

function thinkLine(c) {
  if (c.pos_in_reply > 0) return "Batch: sent in the same LLM reply as the previous call (think gap 0).";
  const cold = c.base_cold ? c.base_added_ms : null;
  let t = "LLM think " + fmtMs(c.think_ms) + " vs Normal cold start ";
  if (cold === null) t += "(not measurable: Normal found a warm pod).";
  else t += fmtMs(cold) + (c.think_ms >= cold ? ": long enough to hide a start begun at the previous call."
                                              : ": too short to hide it.");
  return t;
}

function onCall(c) {
  S.calls.push(c);
  delete S.pending[c.reply + ":" + c.pos_in_reply];
  const bad = c.note || c.results_match === false || !c.pred_ok;
  pill("⚙ " + plain(c.tool) + (c.note ? " (unknown tool)" : "") +
       (c.tool_call_from_text ? " (recovered from text)" : ""), bad ? "bad" : "");
  const tl = $("think-list");
  if (tl) { const d = el("div", "dec", "t" + c.turn + " " + plain(c.tool) + ": " + thinkLine(c)); tl.prepend(d); }
  renderTimelines();
}

function explain(c, k) {
  if (c.note) return plain(c.tool) + ": unknown tool, nothing to predict; it behaved like Normal.";
  const ms = fmtMs(c[k + "_wall_ms"]), added = fmtMs(c[k + "_added_ms"]);
  if (k === "base") return c.base_cold ? plain(c.tool) + ": cold. The pod was started on demand, adding " + added + " (" + ms + " in total)."
    : plain(c.tool) + ": warm. The pod was still up from an earlier call (" + ms + ").";
  if (c.pos_in_reply > 0 && c.pred_cold) return plain(c.tool) + ": batch. It was sent in the same reply as the previous call, so there was nothing to predict in between; cold, " + added + " added.";
  if (c.pred_predicted && !c.pred_cold) return plain(c.tool) + ": started early because it was predicted " +
    (c.pred_warm_source === "hint" ? "by the turn-start hint" : "after the previous call") +
    (c.pos_in_reply === 0 ? " (the LLM thought for " + fmtMs(c.think_ms) + ")" : "") + "; warm on arrival, " + ms + ".";
  if (c.pred_predicted && c.pred_cold) return plain(c.tool) + ": predicted, but too late. The pod was still starting: " +
    added + " added after " + fmtMs(c.think_ms) + " of LLM think time.";
  if (!c.pred_cold) return plain(c.tool) + ": warm. The pod was still up from an earlier call (" + ms + ").";
  return plain(c.tool) + ": not predicted. Cold start, " + added + " added.";
}

function renderTimelines() {
  const max = Math.max(1000, ...S.calls.flatMap((c) => [c.base_wall_ms || 0, c.pred_wall_ms || 0]));
  for (const k of ["base", "pred"]) {
    const tl = $("tl-" + k); tl.textContent = "";
    if (!S.calls.length) { tl.append(el("div", "empty cap", "Tool calls appear here.")); continue; }
    for (const c of S.calls) {
      const r = el("div", "trow"); r.tabIndex = 0;
      const ok = c[k + "_ok"] !== false && c[k + "_wall_ms"] !== undefined;
      r.append(el("span", "lbl", plain(c.tool)), el("span", "ms", ok ? fmtMs(c[k + "_wall_ms"]) : "failed"));
      const st = !ok ? "none" : c[k + "_cold"] ? "cold" : "warm";
      const bb = el("div", "barbox"); const bar = el("div", "bar " + st);
      bar.style.width = (85 * (c[k + "_wall_ms"] || 0) / max) + "%";
      bb.append(bar, el("span", "wl " + st, st === "warm" ? "● warm" : st === "cold" ? "▲ cold" : "✕ failed"));
      r.append(bb);
      const show = () => { const x = $("explain"); x.hidden = false;
        x.textContent = (k === "base" ? "Normal · " : "Skypiea · ") + explain(c, k);
        document.querySelectorAll(".trow.sel").forEach((e) => e.classList.remove("sel")); r.classList.add("sel"); };
      r.onclick = show; r.onkeydown = (e) => { if (e.key === "Enter") show(); };
      tl.append(r);
    }
    tl.scrollTop = 1e9;
  }
}

function renderSummary(T) {
  const b = T && T.base, p = T && T.pred;
  if (!b || !p || !p.ok) { $("sum-text").textContent = "No tool calls yet. Ask Skye something to start.";
    $("sum-n").textContent = ""; $("sum-saved").textContent = ""; return; }
  const d = b.wait_ms - p.wait_ms, w = p.idle_pod_s - b.idle_pod_s;
  const dc = (p.idle_cost_usd || 0) - (b.idle_cost_usd || 0);
  $("sum-text").textContent = "This conversation: you waited " + secs(b.wait_ms) + " on tools with Normal and " +
    secs(p.wait_ms) + " with Skypiea (" + secs(Math.abs(d)) + (d >= 0 ? " less" : " more") + "). " +
    "Skypiea kept pods warm " + Math.abs(w).toFixed(1) + " s " + (w >= 0 ? "longer" : "shorter") +
    " (cost " + (dc >= 0 ? "+" : "−") + fmtUsd(Math.abs(dc)) + ", estimate).";
  $("sum-n").textContent = "n = " + p.ok + " tool call" + (p.ok === 1 ? "" : "s") + " · demo run, not a result";
  const s = $("sum-saved"); s.textContent = "time saved " + (d >= 0 ? "+" : "−") + secs(Math.abs(d));
  s.className = "saved " + (d >= 0 ? "pos" : "neg");
}

function renderBooking(st, writes) {
  const body = $("bk-body"); body.textContent = "";
  if (!st || !st.available) { body.textContent = "Booking record is shown for the Skypiea Air demo."; }
  else if (!st.verified) { body.textContent = "No verified customer yet."; }
  else {
    body.append(el("div", "bk", st.verified.name + " · " + st.verified.customer_id + " (verified)"));
    for (const b of st.bookings) {
      const d = el("div", "bk"); d.append(el("b", "", b.booking_code + " "));
      d.append(el("span", b.status === "cancelled" ? "st-cancelled" : "", b.status));
      d.append(document.createTextNode(" · " + b.fare + " · " + b.bags + " bag(s) · paid $" + b.paid +
        (b.refunded ? " · refunded" : "") + " · " +
        b.flights.map((f) => f.flight_no + " " + f.origin + "→" + f.destination + " " + f.date + " " + f.depart +
          (f.status !== "scheduled" ? " (" + f.status + ")" : "")).join(", ")));
      body.append(d);
    }
  }
  const log = $("bk-log"); log.textContent = "";
  for (const w of [...(writes || [])].reverse()) {
    const d = el("div"); d.append(el("span", "w-" + w.outcome, w.outcome === "success" ? "✓ done" :
      w.outcome === "quote" ? "… asked to confirm" : "✕ refused"));
    d.append(document.createTextNode(" " + w.tool + " (turn " + w.turn + "): " + w.what)); log.append(d);
  }
}

function onTruth(t) {
  const m = S.lastAgent; if (!m || !t) return;
  if (t.verdict === "not_done") for (const b of t.badges) m.append(el("span", "truth", "⚠ " + b));
  else if (t.verdict === "agree") m.append(el("span", "truth ok", "✓ matches the booking record"));
  if (t.earlier && t.earlier.length) m.append(el("span", "truth ok", "✓ done in an earlier turn"));
}

function renderTicker(decs) {
  const box = $("ticker"); box.textContent = "";
  const starting = Object.values(S.grid.pred || {}).some((c) => c.state === "starting");
  if (starting) { const r = el("span", "ring"); r.title = "a Skypiea pod is starting"; box.append(r); }
  if (S.model && S.model.status && S.model.status !== "ok") {
    box.append(document.createTextNode("Predictor is down: Skypiea behaves like scale-to-zero; calls still work.")); return; }
  const d = (decs || S.decs || []).slice(-1)[0];
  if (!d) { box.append(document.createTextNode("Thinking: waiting for the first call.")); return; }
  if (d.outcome !== "ok") { box.append(document.createTextNode("Predictor: " + d.outcome)); return; }
  const top = (d.top3 || []).slice(0, 2).map((x) => plain(x.tool_class) + " " + Math.round(100 * x.p) + "%").join(", ");
  box.append(document.createTextNode("Next likely: " + (top || "?") + " → " +
    ((d.warmed || []).length ? "warming " + d.warmed.map(plain).join(", ") : "not confident: nothing warmed") +
    (starting ? " (pod starting)" : "")));
}

function renderHealth() {
  const parts = [], now = Date.now();
  const cl = S.podsT && now - S.podsT < 5000; parts.push("cluster " + (cl ? "ok" : "no data"));
  const pr = S.model && S.model.status === "ok"; parts.push("predictor " + (pr ? "ok" : "down"));
  parts.push("LLM " + (S.llmState || "not used yet"));
  const links = S.links && Object.values(S.links).some((v) => v !== "ok");
  if (links) parts.push("gateway link down");
  const h = $("health"); h.title = parts.join(" · ");
  const bad = !cl || S.llmState === "error", warn = !pr || links;
  h.className = "health " + (bad ? "bad" : warn ? "warn" : "ok");
  $("health-txt").textContent = bad ? "problem" : warn ? "degraded" : "healthy";
}
setInterval(renderHealth, 2000);

function tile(k, v, sub) { const t = el("div", "tile"); t.append(el("div", "v", v), el("div", "k", sub)); return t; }

function renderTotals(T) {
  S.totals = T; renderSummary(T);
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
  S.grid = grid; S.podsT = Date.now(); renderTicker();
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
  S.decs = decs; renderTicker(decs);
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
  footer(); renderTicker();
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
  S.calls = []; S.replyEl = null; S.agentEl = null; S.lastAgent = null; S.decs = []; S.typingEl = null;
  $("chat").textContent = ""; $("predictor").textContent = ""; $("turns").textContent = "";
  $("think-list").textContent = ""; $("explain").hidden = true;
  const e = el("div", "empty"); e.id = "chat-empty";
  e.append("Say hello to Skye, or press Autopilot.", el("br"),
           el("span", "cap", "Skye is a fictional Skypiea Air assistant (local LLM). The airline data is a mock database."));
  $("chat").append(e); renderBooking({ available: (S.demo.name || "skyair") === "skyair" }, []); renderTicker([]);
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
      { const cv = ev.conversation || {}, last = {}, calls = cv.calls || [], chat = cv.chat || [];
        const turns = [...new Set([...chat.map((m) => m.turn), ...calls.map((c) => c.turn)])].sort((a, b) => a - b);
        for (const t of turns) {     // user text, then the turn's tool calls, then Skye's text
          for (const m of chat.filter((x) => x.turn === t && x.role === "user")) addMsg("user", m.text, "Customer · turn " + t);
          for (const c of calls.filter((x) => x.turn === t)) onCall(c);
          for (const m of chat.filter((x) => x.turn === t && x.role !== "user")) last[t] = addMsg("agent", m.text, "Skye");
        }
        for (const t of cv.truth || []) { S.lastAgent = last[t.turn]; onTruth(t); }
        S.lastAgent = null; }
      for (const t of (ev.conversation ? ev.conversation.turns || [] : [])) renderTurn(t);
      for (const a of (ev.conversation ? ev.conversation.abandoned || [] : [])) renderAbandoned(a);
      if (ev.conversation) { renderTotals(ev.conversation.totals);
        renderBooking(ev.conversation.booking, ev.conversation.writes); }
      for (const e of Object.values(ev.last || {})) handle(e);
      break;
    case "settings": applySettings(ev.settings); break;
    case "status": status(ev.text, ev.level); break;
    case "pods": renderGrid(ev.grid); break;
    case "slow":
      onModel(ev.model); S.links = ev.links;
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
      addMsg("user", ev.text, (ev.source === "autopilot" ? "Autopilot customer" :
             ev.source === "replay" ? "Replay" : "Customer") + " · turn " + ev.turn);
      break;
    case "hint":
      pill("hint sent (" + (ev.source || "enter") + "): metadata only", "hint");
      break;
    case "booking": renderBooking(ev.state, ev.writes); break;
    case "truth": onTruth(ev.truth); break;
    case "turn": renderTurn(ev.turn_record); break;
    case "abandoned": renderAbandoned(ev.abandoned); break;
    case "llm_start": status("Skye is thinking…"); showTyping(); break;
    case "llm_delta":
      dropTyping();
      if (!S.agentEl) { S.agentEl = addMsg("agent", "", "Skye"); S.lastAgent = S.agentEl; }
      S.agentEl.append(document.createTextNode(ev.text)); $("chat").scrollTop = 1e9; break;
    case "llm_reply":
      dropTyping(); S.llmState = "ok"; renderHealth();
      status("Skye replied in " + fmtMs(ev.think_ms) + (ev.n_calls ? ": " + ev.n_calls + " tool call(s)" : ""));
      if (S.agentEl && S.agentEl.textContent.trim() === "Skye") S.agentEl.remove();
      S.agentEl = null;
      break;
    case "call_start": status("calling " + ev.tool + " on both stacks…"); break;
    case "call": onCall(ev.call); break;
    case "status_turn": status(ev.text); break;
    case "turn_end": status("your turn"); break;
    case "error": dropTyping();
      if (ev.where === "llm") { S.llmState = "error"; renderHealth(); }
      status((ev.where === "llm" ? "The language model did not answer: " : ev.where + ": ") + ev.detail, "error"); break;
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
$("btn-details").onclick = () => { const d = $("drawer"); d.hidden = !d.hidden;
  $("btn-details").setAttribute("aria-expanded", String(!d.hidden)); };
$("btn-close").onclick = () => { $("drawer").hidden = true; };
$("btn-help").onclick = () => { $("help").hidden = false; $("btn-help-close").focus(); };
$("btn-help-close").onclick = () => { $("help").hidden = true; };
$("help").onclick = (e) => { if (e.target === $("help")) $("help").hidden = true; };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { $("help").hidden = true; $("drawer").hidden = true; } });
$("btn-grafana").href = location.protocol + "//" + location.hostname + ":3001/";
async function loadPersonas() {
  const box = $("persona-list");
  try {
    const r = await (await fetch("/api/personas")).json(); box.textContent = "";
    box.append(el("div", "cap", "Fictional customers of " + r.airline + ". Click a request to put it in the input."));
    for (const p of r.personas) {
      const d = el("div", "persona");
      d.append(el("b", "", p.name), el("div", "cap", [p.email, p.booking_code, p.fare, p.note].filter(Boolean).join(" · ")));
      for (const q of p.requests || []) { const b = el("button", "", q); b.type = "button";
        b.onclick = () => { const i = $("chat-input"); i.value = q; i.dispatchEvent(new Event("input")); i.focus();
          $("persona").open = false; }; d.append(b); }
      box.append(d);
    }
  } catch (e) { box.textContent = "Personas are not available right now."; }
}
loadPersonas();
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
// ?details=1 / ?help=1 open the drawer / the help overlay (screenshots)
{ const q = new URLSearchParams(location.search);
  if (q.has("details")) $("drawer").hidden = false;
  if (q.has("help")) $("help").hidden = false; }
