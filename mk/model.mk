# Prediction-engine targets (feat/model). Include from the root Makefile with
#   include mk/model.mk
# (the root Makefile is owned by feat/infra; until it includes this: make -f mk/model.mk ...).
PY ?= .venv/bin/python
NICE ?= nice -n 10
COLD_START ?= $(HOME)/skypiea-infra/configs/cold_start.yaml
MODEL ?= $(firstword $(wildcard models/lgbm-coding-*/) models/lgbm-tracelab-dev/)

.PHONY: redteam audit loadtest model-all predictor-image

redteam:  ## red-team suite (tests/redteam, under 10 minutes)
	$(NICE) $(PY) -m pytest -q tests/redteam

audit:  ## no session data on disk, in logs or metrics; kill-during-reload chaos check
	$(NICE) $(PY) tools_audit/audit_no_persistence.py --model $(MODEL) --cold-start $(COLD_START)

loadtest:  ## open loop 75 and 150 req/s (2 workers), closed loop 10 and 200
	$(NICE) $(PY) -m services.predictor.loadtest --model $(MODEL) --cold-start $(COLD_START) --source tracelab --workers 2 --rate 75 --seconds 60
	$(NICE) $(PY) -m services.predictor.loadtest --model $(MODEL) --cold-start $(COLD_START) --source tracelab --workers 2 --rate 150 --seconds 60
	$(NICE) $(PY) -m services.predictor.loadtest --model $(MODEL) --cold-start $(COLD_START) --source tracelab --workers 2 --concurrency 10 --seconds 30
	$(NICE) $(PY) -m services.predictor.loadtest --model $(MODEL) --cold-start $(COLD_START) --source tracelab --workers 2 --concurrency 200 --seconds 60

model-all:  ## tune on val, simulate on test, export the coding bundle, test
	$(NICE) $(PY) -m sim.tune --profile support --cold-start $(COLD_START)
	$(NICE) $(PY) -m sim.tune --profile coding --cold-start $(COLD_START)
	$(NICE) $(PY) -m sim.offline --cold-start $(COLD_START)
	$(NICE) $(PY) -m sim.cold_decomp --cold-start $(COLD_START)
	$(PY) -m sim.offline --figures
	$(NICE) $(PY) -m services.predictor.export --source tracelab --profile coding --cold-start $(COLD_START)
	$(PY) -m ruff check .
	$(NICE) $(PY) -m pytest -q

predictor-image:  ## docker build of the service with $(MODEL)
	docker build -f services/predictor/Dockerfile --build-arg MODEL_DIR=$(MODEL) -t skypiea/predictor:$$(git rev-parse --short=12 HEAD) .
