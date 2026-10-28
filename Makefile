.PHONY: test lint cluster deploy e1 clean-cluster

CLUSTER ?= skypiea

test:
	pytest -q

lint:
	ruff check .

cluster:
	kind create cluster --name $(CLUSTER) --config deploy/kind/cluster.yaml

deploy:
	kubectl apply -f deploy/k8s/

e1:
	python -m experiments.run --config experiments/e1_predictor_accuracy.yaml

clean-cluster:
	kind delete cluster --name $(CLUSTER)
