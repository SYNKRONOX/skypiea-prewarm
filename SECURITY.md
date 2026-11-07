# Security policy

Skypiea is a course project (DS252, IISc). It is not production software and has no supported
releases beyond the tags in [`CHANGELOG.md`](CHANGELOG.md).

## Reporting a vulnerability

Please do **not** open a public issue with details. Use GitHub's private vulnerability report
(repository **Security** tab, **Report a vulnerability**) if it is enabled. If it is not, open
an issue titled `security: contact request` without any details, and a maintainer will reply
with a private channel.

Useful to include: the commit (`git rev-parse --short HEAD`), the component (gateway,
controller, predictor, console, chart, Terraform, scripts) and how to reproduce it.

## The Live Console is a local demo with no authentication

The Live Console (`make live`, http://127.0.0.1:8090) has **no login, no token and no user
accounts**. Its `POST` endpoints change state for everyone looking at it: `POST /api/hint`
(turn-start hint), `/api/policy` (baseline policy), `/api/model` (switch LLM), `/api/predictor`
(kill or restart the predictor), `/api/reset`, `/api/replay`, `/api/lookahead`, `/api/mirror`,
`/api/cpm`, `/api/typing`, `/api/demo`, `/api/chat`, `/api/autopilot`, `/api/story`. The same holds for the
port-forwarded gateway (`POST /mcp`, `POST /v1/hint`) and controller (`POST /v1/warm`).

- **It must listen on 127.0.0.1 only.** It does: `console/app.py` `main()` calls
  `uvicorn.run(..., host="127.0.0.1", ...)` and there is no option to change the address;
  `tests/test_console_security.py::test_console_binds_to_loopback_only` checks it. Every
  port-forward is started with `--address 127.0.0.1` (`demo/portforward.sh`,
  `demo/live_stacks.sh`). Checked on 2026-11-07 with `ss -ltn`: the console (:8090),
  Grafana (:3001) and the gateways (:18180, :18280) listen on `127.0.0.1` only.
- **Do not expose it.** No reverse proxy, no `ssh -R`, no tunnel, no `0.0.0.0` port-forward,
  no shared machine where other users can reach your loopback.
- **Known gap: cross-site requests.** The console does not check the `Origin` header or the
  request's content type, so a web page open in the same browser could send it a settings
  change (for example kill the predictor). Close other tabs while presenting. The fix is
  requested in [`docs/handoff/REQUESTS_W2.md`](docs/handoff/REQUESTS_W2.md) (R9); an expected-fail
  test (`tests/test_console_security.py::test_cross_site_post_is_refused`) shows it.
- Its data is a fictional mock world (`docs/SKYPIEA_WORLD.md`): there is no real customer data
  to leak. The LLM runs locally (Ollama) unless you point `AGENT_BASE_URL` elsewhere.

More known gaps and their workarounds: [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md).

## What the project already does

- **No inbound ports on AWS.** The EC2 node is reached only through SSM Session Manager; no SSH,
  no key pair, outbound TCP 443 only, IMDSv2 required, encrypted root volume, a least-privilege
  instance role ([`infra/aws/README.md`](infra/aws/README.md), [`docs/aws_plan.md`](docs/aws_plan.md)).
- **AWS targets refuse by default.** Every `make aws-*` target stops before any AWS or Terraform
  call without the CLIs, credentials and local Terraform state; creating targets also need
  `AWS_CONFIRM=yes` (`infra/aws/scripts/aws-guard.sh`, `tests/test_aws_guard.py`).
- **Local services listen on 127.0.0.1 only** (port-forwards, Live Console); all cluster
  Services are ClusterIP ([`dev.md`](dev.md)).
- **No secrets in the repository.** `make secret-sweep HISTORY=1` scans the tree and every
  commit for credential-like text, unmasked AWS account ids and secret-like file names, and
  prints file names only (`tools/secret_sweep.sh`). Terraform state, plans and kubeconfigs are
  git-ignored. On 2026-11-06 the sweep found nothing outside its allow-list.
- **Metadata only.** The predictor receives tool names, timings and batch structure, never
  prompts or arguments; `make audit` checks that no session data is kept
  ([`docs/predictor_contract.md`](docs/predictor_contract.md), [`docs/redteam.md`](docs/redteam.md)).
- **Model downloads are verified.** `make fetch-models` accepts https only (http for
  localhost tests), checks the archive against the release's `SHA256SUMS` and every file
  against `configs/live/models_files.sha256`, and refuses unsafe archive paths.

## Dependencies

Python dependencies are pinned in `requirements.lock` (and the image constraint files). We did
**not** run a vulnerability audit of the pins: no advisory database or `pip-audit` is available
offline in this repository, and none was added. To check them yourself:
`pip install pip-audit && pip-audit -r requirements.lock` (this sends the package list to the
PyPI/OSV advisory services).
