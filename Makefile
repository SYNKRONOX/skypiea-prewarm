.PHONY: test lint check-sizes install-hooks cluster deploy e1 clean-cluster

CLUSTER ?= skypiea

test:
	pytest -q

lint:
	ruff check .

check-sizes:
	python tools/check_sizes.py

install-hooks:
	cp tools/pre-commit.sh .git/hooks/pre-commit
	chmod +x .git/hooks/pre-commit

cluster:
	kind create cluster --name $(CLUSTER) --config deploy/kind/cluster.yaml

deploy:
	kubectl apply -f deploy/k8s/

e1:
	python -m experiments.run --config experiments/e1_predictor_accuracy.yaml

clean-cluster:
	kind delete cluster --name $(CLUSTER)
