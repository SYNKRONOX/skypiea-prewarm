.PHONY: test lint check-sizes install-hooks data data-all samples cluster deploy e1 clean-cluster

# Infra targets (tool images, kind, AWS, E0, gateway/controller): see mk/infra.mk and docs/infra.md
include mk/infra.mk

CLUSTER ?= skypiea
SOURCE ?= tracelab
SOURCES = tracelab copilot_2026 tau_bench tau2_bench bfcl

test:
	pytest -q

lint:
	ruff check .

check-sizes:
	python tools/check_sizes.py

install-hooks:
	cp tools/pre-commit.sh .git/hooks/pre-commit
	chmod +x .git/hooks/pre-commit

data:
	python -m traces.pipeline --source $(SOURCE)

data-all:
	for s in $(SOURCES); do python -m traces.pipeline --source $$s || exit 1; done

samples:
	python tools/make_sample.py --all

cluster: cluster-kind          # kind (1 control-plane + 1 worker) + local registry localhost:5001

deploy: deploy-kind            # helm install skypiea (tools at 0, gateway, controller, monitoring)

e1:
	python -m experiments.run --config experiments/e1_predictor_accuracy.yaml

clean-cluster: cluster-kind-down

# Prediction-engine targets (predictor, simulator, results summary): see mk/model.mk and docs/model.md
include mk/model.mk
