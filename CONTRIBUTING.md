# Contributing

Skypiea is a DS252 course project (IISc). Issues and pull requests are welcome, especially
reports of anything in the README that is broken, confusing or untrue.

## Reporting a problem

Open an issue with the **Bug report** template: environment (paste the output of
`make report`: commit, OS, versions, `make doctor` and the last test summary, with no secrets
and your home directory shown as `~`), steps, expected, actual. `make review` runs Tier 0 and
Tier 1 and prints a PASS/FAIL table you can paste too. How we triage: [`docs/TRIAGE.md`](docs/TRIAGE.md). Check README "Known limitations" first. Security problems: [`SECURITY.md`](SECURITY.md).

## Working on the code

```bash
make doctor DOCTOR_MODE=test   # Python 3.12, make, git
make setup                     # .venv from requirements.lock
make install-hooks             # pre-commit hook: no file over 50 MB
make test lint check-sizes     # what CI's test job runs
make demo                      # what CI's e2e job runs (needs Docker, kind, kubectl, helm)
```

How the code fits together, and how to add a tool server, a baseline policy or a dataset:
[`dev.md`](dev.md). Every check and how CI runs it: [`docs/TESTING.md`](docs/TESTING.md).

## Rules for changes

- **Numbers come from committed files.** Every number in a doc names its source file
  ([`docs/numbers_and_sources.md`](docs/numbers_and_sources.md)). Do not type a number that is
  not in the repository; demo runs on kind are labelled "demo run, not results".
- **No datasets, bundles, secrets or credentials in git.** Raw and processed data stay in
  `data/raw/` and `data/processed/` (git-ignored); predictor bundles in `models/` (git-ignored);
  `make secret-sweep` before you push.
- **AWS targets stay guarded.** A new target that calls AWS starts with `@$(AWS_GUARD) $@`
  (or `@$(AWS_GUARD_CREATE) $@` if it creates or starts resources); `tests/test_aws_guard.py`
  fails otherwise.
- **Dependencies are pinned.** Add a package to `requirements-dev.txt` (or the image's
  requirements), then `make lock`, and say why in the pull request.
- **`PRD.md` is the original plan and is not edited.** Changes to what we met go in
  [`docs/PRD_STATUS.md`](docs/PRD_STATUS.md).
- Keep `ruff check .` and `pytest -q` green.
