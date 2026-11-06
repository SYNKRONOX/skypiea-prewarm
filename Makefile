.PHONY: doctor secret-sweep setup lock test lint check-sizes install-hooks data data-all samples cluster deploy e1 clean-cluster

# Infra targets (tool images, kind, AWS, E0, gateway/controller): see mk/infra.mk and docs/infra.md
include mk/infra.mk

CLUSTER ?= skypiea
SOURCE ?= tracelab
SOURCES = tracelab copilot_2026 tau_bench tau2_bench bfcl

# One venv for the whole repo (Python 3.12), installed from the lock file.
PYTHON ?= python3.12
CONSTRAINTS = -c deploy/platform/constraints.txt -c tools/common/constraints.txt \
              -c services/predictor/requirements.lock

DOCTOR_MODE ?= all
doctor:                  ## check OS, Docker (running, memory), kind, kubectl, helm, Python 3.12, disk, ports, Ollama
	@tools/doctor.sh $(DOCTOR_MODE)

secret-sweep:            ## credentials / AWS account ids in the tree (HISTORY=1: every commit); prints file names only
	@tools/secret_sweep.sh

setup:                   ## create .venv and install requirements.lock (runtime + dev)
	@test -x .venv/bin/python || command -v $(PYTHON) >/dev/null 2>&1 || { \
	  echo "make setup: $(PYTHON) not found. Install Python 3.12 (make doctor prints the command)" \
	       "or point to it: make setup PYTHON=/path/to/python3.12" >&2; exit 1; }
	test -x .venv/bin/python || $(PYTHON) -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q -r requirements.lock
	.venv/bin/pip check

lock:                    ## re-resolve requirements-dev.txt against the image pins -> requirements.lock
	.venv/bin/pip install -q -r requirements-dev.txt $(CONSTRAINTS)
	.venv/bin/pip check
	{ echo "# Full resolved set for .venv (pip freeze, Python 3.12), resolved against the image"; \
	  echo "# constraint files. Regenerate: make lock.  Install: make setup."; \
	  .venv/bin/pip freeze; } > requirements.lock

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check .

check-sizes:
	$(PY) tools/check_sizes.py

install-hooks:
	cp tools/pre-commit.sh .git/hooks/pre-commit
	chmod +x .git/hooks/pre-commit

data:
	$(PY) -m traces.pipeline --source $(SOURCE)

data-all:
	for s in $(SOURCES); do $(PY) -m traces.pipeline --source $$s || exit 1; done

samples:
	$(PY) tools/make_sample.py --all

cluster: cluster-kind          # kind (1 control-plane + 1 worker) + local registry localhost:5001

deploy: deploy-kind            # helm install skypiea (tools at 0, gateway, controller, monitoring)

e1:
	$(PY) -m experiments.run --config experiments/e1_predictor_accuracy.yaml

clean-cluster: cluster-kind-down

# Prediction-engine targets (predictor, simulator, results summary): see mk/model.mk and docs/model.md
include mk/model.mk

# Demo (kind, functional) and the live-LLM agent demo: see mk/demo.mk, docs/DEMO.md, docs/AGENT.md
include mk/demo.mk
