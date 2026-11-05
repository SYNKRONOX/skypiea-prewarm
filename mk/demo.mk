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
