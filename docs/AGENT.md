# Live agent demo: an LLM calling Skypiea tools through the gateway

`agent/` is a small tool-calling agent. It talks to the MCP tools **only** through the
gateway's public endpoint `POST /mcp`, the same way any MCP client would. It imports nothing
from `gateway/` or `controller/`; a test checks this. Its LLM is either an
**OpenAI-compatible** endpoint (for example Ollama on your laptop) or a **scripted** stand-in
for CI and offline demos.

> Both scenarios use predictor profile **`prd`, which is SYNTHETIC**: it was trained on traces
> sampled from the PRD scenario graphs, not on real agent traces. We cannot use the `coding`
> profile for Scenario B, because its vocabulary has no `format_diff`. Every output of the agent
> says this.

## Pieces

| File | What it does |
|---|---|
| `agent/llm.py` | `OpenAICompatibleLLM`: `POST {AGENT_BASE_URL}/chat/completions` with `tools`, `temperature: 0`. Configured by `AGENT_BASE_URL`, `AGENT_MODEL` and an optional `AGENT_API_KEY`, which is sent as a Bearer token and never printed or stored. `ScriptedLLM`: a fixed tool sequence with a configurable fake think time. A reply without tool calls raises `NoToolCallError` with a clear message |
| `agent/mcp_client.py` | MCP over Streamable HTTP (protocol 2026-07-28): keeps the `Mcp-Session-Id` the gateway assigns, has timeouts, reads the gateway's `X-Skypiea-Cold / -Added-Ms / -Held-Ms / -Predicted` headers, and ends the session with `DELETE` |
| `agent/loop.py` | The loop: LLM step, tool calls, results back to the LLM, until `finish` or the step limit (default 8). Arguments outside a tool's schema are dropped, and an unknown tool name is not sent. If the model answers in text instead of calling a tool, it gets a fixed simulated-user reply ("the user confirms; continue; call `finish` when done"), at most twice per session; the session then ends with `no_tool_call` |
| `agent/tasks/` | Scenario A `support_refund` (order_lookup, policy_check, refund_issue) and Scenario B `coding` (read_file, run_tests, format_diff, post_comment). The system prompt describes the tools, and **the LLM picks its own order**. Each session records whether its order is a walk of the PRD scenario graph (`followed_graph`) and whether it is exactly the canonical chain (`canonical_order`) |
| `agent/run.py` | Runs N sessions of one scenario under one policy and records them (below) |
| `agent/platform.py` | The experiment harness, which is not part of the agent: it sets the stub policy, waits until every tool is back at 0 replicas, and reads idle pod-seconds from the controller |
| `agent/summarize.py` | `summary.csv`, `summary_think.csv`, `summary.md` |
| `agent/probe.py` | Sends a few calls through `/mcp` (used by `make demo-failures`) |

### What is recorded (metadata only)

`<run_id>_steps.csv` has one row per tool call: run, scenario, policy, LLM kind, model,
profile, session, step, LLM think ms before the call, tool, ok, cold, predicted (the tool was
in the predictor's last warm decision), added ms, held ms, wall ms, how many arguments were
dropped, and the error. `<run_id>_sessions.csv` has one row per session: tool order, graph
followed, canonical order, nudges and end reason. `<run_id>_run.json` holds the labels, idle
pod-seconds, the predictor version, and checks. **No prompts, arguments or tool payloads are
written.**

## Commands

These need the stack from `make demo` (cluster up, port-forwards running).

```bash
# offline LLM (ScriptedLLM, 3 s fake think time): no LLM network at all
make agent-demo                          # Scenario A and B, 3 sessions each, policy predictive
make agent-demo AGENT_N=5

# live LLM, any OpenAI-compatible endpoint
export AGENT_BASE_URL=http://localhost:11434/v1   # Ollama
export AGENT_MODEL=llama3.1:8b
make agent-demo LLM=openai-compatible
```

`make agent-demo` writes to `.demo/agent_runs/` (functional demo, not results). The policy
must match what the gateway serves. `make demo` leaves the gateway serving the **model**, which
is right for `POLICY=predictive`. For the stub policies, switch first:

```bash
SERVE=stub  PRED_TAG=<tag> demo/deploy.sh && demo/portforward.sh start
make agent-demo POLICY=scale_to_zero     # or POLICY=keep_alive (30 s)
SERVE=model PRED_TAG=<tag> demo/deploy.sh && demo/portforward.sh start
```

Here `<tag>` is the predictor image tag: `prd-demo-<hash>` from `make demo` (shown in its step
3), or `a7642d03dd10` for the shipped prd bundle. If you get it wrong, `agent/run.py` notices:
a run is flagged when the model saw `/decide` requests during a stub-policy run, or none
during a predictive run.

One run, by hand:

```bash
.venv/bin/python -m agent.run --scenario A --policy predictive --llm openai-compatible \
    --sessions 3 --out-dir .demo/agent_runs
.venv/bin/python -m agent.summarize --dir .demo/agent_runs --llm openai-compatible
```

### Local Ollama, step by step

1. Install Ollama (on Windows, or inside WSL/Linux), then run `ollama pull llama3.1:8b`. The
   model is about 4.9 GB and supports tools.
2. Check that the agent's machine can reach it: `curl http://localhost:11434/v1/models` should
   list `llama3.1:8b`. For Ollama on Windows with the agent in WSL2, see
   [DEMO.md, Troubleshooting](DEMO.md#troubleshooting) (mirrored vs NAT networking,
   `OLLAMA_HOST=0.0.0.0`).
3. `export AGENT_BASE_URL=http://localhost:11434/v1 AGENT_MODEL=llama3.1:8b`. Ollama needs no
   key, so leave `AGENT_API_KEY` unset.
4. `make agent-demo LLM=openai-compatible`.

## The full live run (STEP 2 of v1.1)

```bash
make agent-live          # AGENT_N=10 by default; Ollama + llama3.1:8b by default
```

`demo/agent_live.sh` deploys the gateway serving the stub, runs Scenario A and B under
`scale_to_zero` and `keep_alive` (30 s), then switches to the model and runs both under
`predictive`, with 10 sessions in each cell. Sessions run one after another, at real
(unscaled) LLM think times. Every run waits until all tools are back at 0 before and after it.
The predictor is the shipped prd bundle (`a7642d03dd10`) if the local registry has it,
otherwise the demo bundle. Everything goes to `results/agent_runs/`, and the summary is in
`results/agent_runs/summary.md`.

The results and their limits are in the README ("Demo") and in
`docs/handoff/AGENT_REPORT.md`.

## Tests

`tests/test_agent.py` is offline and deterministic. It covers the MCP client against a fake
gateway (session id, headers, DELETE), scripted sessions for A and B, the graph check, unknown
tools and dropped arguments, the step limit, the OpenAI client (temperature 0, Bearer key, a
clear error when there is no tool call, the key never in `repr` or errors), the nudge, the
summary arithmetic, and that `agent/` imports nothing from `gateway/` or `controller/`. The
agent needs no new dependency; it uses `httpx`, which is already in `requirements.lock`.
