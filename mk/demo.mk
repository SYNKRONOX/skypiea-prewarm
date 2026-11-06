# Reproducible demo on kind (docs/DEMO.md) and the agent demo (docs/AGENT.md).
.PHONY: demo demo-clean demo-portforward agent-demo agent-live

demo:                    ## kind + images + chart (profile prd) + Scenario A replay: scale_to_zero vs predictive
	PY=$(PY) demo/run.sh

demo-portforward:        ## (re)start the loopback port-forwards (gateway :18080, Grafana :3000)
	demo/portforward.sh start

demo-clean:              ## stop port-forwards, delete the kind cluster + registry, remove .demo/ and the demo bundle
	-demo/portforward.sh stop
	deploy/kind/down.sh
	rm -rf .demo models/lgbm-prd-demo-*
	@echo "Docker images skypiea/* are kept (docker image prune / docker rmi to remove them)."

LLM    ?= scripted
POLICY ?= predictive
agent-demo:              ## agent on Scenario A+B via the gateway (LLM=scripted|openai-compatible, POLICY, N)
	PY=$(PY) LLM=$(LLM) POLICY=$(POLICY) N=$(or $(AGENT_N),3) demo/agent_demo.sh

agent-live:              ## STEP 2: live LLM (Ollama by default), A+B x scale_to_zero/keep_alive/predictive, N=10 each
	PY=$(PY) N=$(or $(AGENT_N),10) demo/agent_live.sh

demo-failures:           ## failure injection: predictor stopped, controller killed; calls must succeed
	PY=$(PY) demo/failure_injection.sh

# ---------------- Live Console (docs/LIVE_CONSOLE.md) ----------------
.PHONY: live live-clean live-pf live-console live-screenshot
live:                    ## both stacks (baseline + predictive) + Live Console on http://127.0.0.1:8090
	PY=$(PY) demo/live_up.sh

live-clean:              ## stop the console, uninstall both stacks (cluster and registry stay)
	demo/live_down.sh

live-pf:                 ## restart the live port-forwards
	demo/live_stacks.sh pf

live-console:            ## run the console in the foreground (stacks must be up)
	$(PY) -m console

live-screenshot:         ## docs/img/console_<name>.png from a locally installed Chrome (NAME=...)
	demo/screenshot.sh docs/img/console_$(or $(NAME),snapshot).png
