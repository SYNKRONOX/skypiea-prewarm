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
