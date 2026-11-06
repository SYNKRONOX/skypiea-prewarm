# Documentation index

One line per document. Start with the top-level [`README.md`](../README.md); release history
is in [`CHANGELOG.md`](../CHANGELOG.md).

## Results and claims

- [`results_summary.md`](results_summary.md): all result tables, generated from the committed result files only.
- [`numbers_and_sources.md`](numbers_and_sources.md): every number we say out loud, with its source file and reason.
- [`limits.md`](limits.md): what the results do not show, in plain language.
- [`experiments.md`](experiments.md): the experiment table: question, data and output of each experiment (E0, E1, ...).
- [`benchmark_protocol.md`](benchmark_protocol.md): re-run of the published "Reading the Plan" next-tool protocol on tau-bench, next to our own harness.
- [`redteam.md`](redteam.md): red-team suite for the predictor service.
- [`VIVA.md`](VIVA.md): viva preparation: pitch, architecture walk-through, demo script, claims with sources, Q&A, slides.

## Design

- [`prd_excerpts.md`](prd_excerpts.md): PRD v1.1 requirement text, verbatim, and our interpretation.
- [`gateway_controller.md`](gateway_controller.md): the MCP gateway, the 150 ms hold and the kopf scale-to-zero operator (cap of 2).
- [`predictor_contract.md`](predictor_contract.md): the gateway-predictor API contract.
- [`model.md`](model.md): prediction engine: graph, LightGBM ranker, warm policy, profiles, simulator, tuning disclosures.
- [`schema.md`](schema.md): trace schema shared by the converters, simulator and replayer.
- [`cold_start_schema.md`](cold_start_schema.md): schema of `configs/cold_start.yaml` (measured cold starts).
- [`infra.md`](infra.md): tool servers, kind and k3s clusters, E0 cold-start harness.
- [`aws_plan.md`](aws_plan.md): the AWS plan as reviewed before apply (resources, prices with sources, security, teardown); the runs themselves are in `results/`.

## Demos (kind, laptop; demo runs, not results)

- [`DEMO.md`](DEMO.md): `make demo` and `make live`: commands, timings, the 7-minute presenter script, troubleshooting.
- [`AGENT.md`](AGENT.md): the tool-calling agent through the gateway (`make agent-demo`, `make agent-live`).
- [`LIVE_CONSOLE.md`](LIVE_CONSOLE.md): Live Console design: vocabularies, two stacks, mirrored execution, hint, panels, lead-time options.
- [`PORTABLE_MODELS.md`](PORTABLE_MODELS.md): proposal for distributing the predictor bundles (`make fetch-models`).
- [`live_backup/README.md`](live_backup/README.md): one recorded Live Console session, for a presentation fallback.
- [`demo_transcript.log`](demo_transcript.log): terminal transcript of a `make demo` run.
<!-- Reserved: LIVE_DEMO.md (added by the live-demo session). -->

## Handoff reports (dated working notes)

- [`handoff/FINAL_DOCS_REPORT.md`](handoff/FINAL_DOCS_REPORT.md): this final documentation pass: what changed and which numbers and links were checked.
- [`handoff/LEADTIME_REPORT.md`](handoff/LEADTIME_REPORT.md): lookahead in the simulator and the 36-conversation lead-time grid.
- [`handoff/LIVE_CONSOLE_REPORT.md`](handoff/LIVE_CONSOLE_REPORT.md): Live Console build and verification runs, bugs found, risks.
- [`handoff/AGENT_REPORT.md`](handoff/AGENT_REPORT.md): v1.1 overnight report: reproducible demo and live-LLM agent runs.
- [`handoff/MORNING_REPORT.md`](handoff/MORNING_REPORT.md): 2026-11-05 AWS scenario runs (keep-alive 10/60 s, Markov-1 rerun).
- [`handoff/OVERNIGHT_REPORT.md`](handoff/OVERNIGHT_REPORT.md): 2026-11-04/05 overnight AWS runs on the infra branch.
- [`handoff/STATE_infra.md`](handoff/STATE_infra.md): infra branch state as of 2026-11-02.
- [`handoff/STATE_model.md`](handoff/STATE_model.md): prediction-engine branch state of work.
- [`handoff/phase2_model.txt`](handoff/phase2_model.txt): raw terminal log of the phase-2 model-branch session (plain text).

## Elsewhere in the repository

- [`../infra/aws/README.md`](../infra/aws/README.md): step-by-step AWS run (Terraform, k3s, ECR, SSM only).
- [`../data/README.md`](../data/README.md): datasets, download and licences (data is not committed).
- [`../results/live_console_demo/README.md`](../results/live_console_demo/README.md): caveats of the Live Console verification runs.
