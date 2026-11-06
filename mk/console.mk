# Live Console and demo-polish targets (W1). Not yet included by the root Makefile: until
# `include mk/console.mk` is added there (docs/handoff/REQUESTS_W1.md), run them with
#   make -f Makefile -f mk/console.mk <target>
.PHONY: fr6-reload hold-report

fr6-reload:              ## FR-6: reload the real model under load, in-flight requests served -> results/fr6_reload_check.json
	$(PY) -m services.predictor.reload_check --out results/fr6_reload_check.json

hold-report:             ## FR-9: explain the held_ms values recorded in the AWS runs
	$(PY) -m gateway.hold_report results/scenario_aws_calls.csv
