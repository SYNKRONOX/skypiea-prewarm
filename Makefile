.PHONY: test lint check-sizes install-hooks data data-all samples cluster deploy e1 clean-cluster

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

cluster:
	kind create cluster --name $(CLUSTER) --config deploy/kind/cluster.yaml

deploy:
	kubectl apply -f deploy/k8s/

e1:
	python -m experiments.run --config experiments/e1_predictor_accuracy.yaml

clean-cluster:
	kind delete cluster --name $(CLUSTER)
