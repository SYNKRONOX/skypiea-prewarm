# Documentation index

One line per document. Start with the top-level [`README.md`](../README.md); release history
is in [`CHANGELOG.md`](../CHANGELOG.md).

## Requirements and developer docs

- [`../PRD.md`](../PRD.md): the original PRD v1.1, unchanged (emails removed).
- [`PRD_STATUS.md`](PRD_STATUS.md): every FR, NFR, PRD experiment and scope item: Met / Partly met / Changed / Not done, with evidence.
- [`../dev.md`](../dev.md): developer guide: request path, ports and metrics, folders, components, tests, how to add a tool, policy or dataset.
- [`TESTING.md`](TESTING.md): every check (doctor, tests, lint, size check, secret sweep, demo, end to end, CI): what it proves and how to reproduce it.
- [`training_data.md`](training_data.md): sessions and calls per split for each predictor profile (generated from the split reports).
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md), [`../SECURITY.md`](../SECURITY.md): how to report a problem or a vulnerability.

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

- [`LIVE_DEMO.md`](LIVE_DEMO.md): the Live Console's Skypiea Air presentation view: how to run it, what is on the screen, the truth panel.
- [`DEMO.md`](DEMO.md): `make demo` and `make live`: commands, timings, the presenter script, troubleshooting.
- [`AGENT.md`](AGENT.md): the tool-calling agent through the gateway (`make agent-demo`, `make agent-live`).
- [`LIVE_CONSOLE.md`](LIVE_CONSOLE.md): Live Console design: vocabularies, two stacks, mirrored execution, hint, panels, lead-time options.
- [`PORTABLE_MODELS.md`](PORTABLE_MODELS.md): how the predictor bundles are distributed (`make fetch-models`, release v1.2 asset).
- [`MODELS_ATTRIBUTION.md`](MODELS_ATTRIBUTION.md): attribution and licences of the predictor bundles.
- [`live_backup/README.md`](live_backup/README.md): one recorded Live Console session, for a presentation fallback.
- [`demo_transcript.log`](demo_transcript.log): terminal transcript of a `make demo` run.

## Handoff reports (dated working notes)

- [`handoff/W2_REPORT.md`](handoff/W2_REPORT.md): red-team readiness pass (doctor, AWS guards, CI e2e, README tiers), with [`DECISIONS_W2`](handoff/DECISIONS_W2.md), [`CHANGES_W2`](handoff/CHANGES_W2.md), [`REQUESTS_W2`](handoff/REQUESTS_W2.md) and [`SELF_REDTEAM`](handoff/SELF_REDTEAM.md).
- [`handoff/CONSOLE_SUPPORT_REPORT.md`](handoff/CONSOLE_SUPPORT_REPORT.md): support console (Skypiea Air, Skye, truth panel) build and verification.
- [`handoff/PHASE_A_HANDOFF.md`](handoff/PHASE_A_HANDOFF.md) and [`handoff/DECISIONS.md`](handoff/DECISIONS.md): Phase A of the support console (branch `feat/console-support`) and its decision log.
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
