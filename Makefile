.DEFAULT_GOAL := help
.PHONY: help doctor review report secret-sweep setup lock test lint check-sizes check-claims test-docker install-hooks data data-all samples cluster deploy e1 clean-cluster

# Infra targets (tool images, kind, AWS, E0, gateway/controller): see mk/infra.mk and docs/infra.md
include mk/infra.mk

CLUSTER ?= skypiea
SOURCE ?= tracelab
SOURCES = tracelab copilot_2026 tau_bench tau2_bench bfcl

# One venv for the whole repo (Python 3.12), installed from the lock file.
PYTHON ?= python3.12
CONSTRAINTS = -c deploy/platform/constraints.txt -c tools/common/constraints.txt \
              -c services/predictor/requirements.lock

help:                    ## list the make targets with their one-line description
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' Makefile mk/*.mk | sort | \
	  awk -F':.*## ' '{printf "  %-24s %s\n", $$1, $$2}'

DOCTOR_MODE ?= all
doctor:                  ## check OS, Docker (running, memory), kind, kubectl, helm, Python 3.12, disk, ports, Ollama
	@PYTHON=$(PYTHON) tools/doctor.sh $(DOCTOR_MODE)

MODE ?= all
review:                  ## Tier 0 then Tier 1 on its own cluster skypiea-review; PASS/FAIL table (MODE=test: Tier 0 only)
	@MODE=$(MODE) tools/review.sh

report:                  ## OS, versions, make doctor, last test summary, to paste into an issue (no secrets, no home paths)
	@tools/report.sh

secret-sweep:            ## credentials / AWS account ids in the tree (HISTORY=1: every commit); prints file names only
	@tools/secret_sweep.sh

setup:                   ## create .venv and install requirements.lock (runtime + dev)
	@test -x .venv/bin/python || command -v $(PYTHON) >/dev/null 2>&1 || { \
	  echo "make setup: $(PYTHON) not found. Install Python 3.12 (make doctor prints the command)" \
	       "or point to it: make setup PYTHON=/path/to/python3.12" >&2; exit 1; }
	@if [ -e .venv ] && ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))' 2>/dev/null; then \
	  echo "make setup: .venv exists but is not Python 3.12. Delete the .venv folder and run make setup again" >&2; exit 1; fi
	@test -x .venv/bin/python || $(PYTHON) -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))' 2>/dev/null || { \
	  echo "make setup: $(PYTHON) is not a working Python 3.12 ($$($(PYTHON) --version 2>&1 | head -1)). Install 3.12" \
	       "(make doctor prints the command) or point to it: make setup PYTHON=/path/to/python3.12" >&2; exit 1; }
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

test:                    ## pytest (committed samples only; no datasets, no Docker)
	$(PY) -m pytest -q

lint:                    ## ruff check .
	$(PY) -m ruff check .

check-sizes:             ## fail if a tracked file is over 50 MB
	$(PY) tools/check_sizes.py

test-docker:             ## Tier 0 in a python:3.12 container (no local Python 3.12 needed; Docker + network)
	@tools/test_docker.sh

check-claims:            ## docs vs evidence: numbers vs result files, links, make targets, paths, commands
	$(PY) tools/check_claims.py

install-hooks:           ## git pre-commit hook: block files over 50 MB
	cp tools/pre-commit.sh .git/hooks/pre-commit
	chmod +x .git/hooks/pre-commit

data:                    ## one dataset raw -> data/processed/$(SOURCE) (needs data/download.py first)
	$(PY) -m traces.pipeline --source $(SOURCE)

data-all:                ## every dataset raw -> data/processed/ (needs the downloads)
	for s in $(SOURCES); do $(PY) -m traces.pipeline --source $$s || exit 1; done

samples:                 ## rebuild data/samples/ from data/processed/
	$(PY) tools/make_sample.py --all

cluster: cluster-kind    ## kind (1 control-plane + 1 worker) + local registry localhost:5001

deploy: deploy-kind      ## push images, helm install (tools at 0, gateway, controller, stub predictor, monitoring)

e1:                      ## E1 next-tool accuracy (needs data/processed)
	$(PY) -m experiments.run --config experiments/e1_predictor_accuracy.yaml

clean-cluster: cluster-kind-down  ## delete the kind cluster and its registry

# Prediction-engine targets (predictor, simulator, results summary): see mk/model.mk and docs/model.md
include mk/model.mk

# Demo (kind, functional) and the live-LLM agent demo: see mk/demo.mk, docs/DEMO.md, docs/AGENT.md
include mk/demo.mk

# Live Console / demo-polish targets owned by the console session (watch-pods, fr6-reload,
# hold-report, ...): see mk/console.mk and docs/LIVE_DEMO.md
include mk/console.mk
