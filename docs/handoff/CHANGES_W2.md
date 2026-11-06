# Changes, week 2 red-team hardening (branch `feat/redteam-ready`)

One line per step. The commit is the one that contains the line.

| Step | Change | Files |
|---|---|---|
| 0 | Handoff logs for this branch | `docs/handoff/{DECISIONS,CHANGES,REQUESTS}_W2.md`, `docs/handoff/SELF_REDTEAM.md` |
| 1 | `make doctor` (`tools/doctor.sh test|demo|live|all`): OS/WSL, Docker running + memory (6 GB, docs/DEMO.md), kind/kubectl/helm, Python 3.12, disk (15 GB, docs/DEMO.md), RAM, ports, bundles, optional LLM; prints the exact fix per FAIL. `make demo` and `make live` run it (quiet) first and stop before changing anything; `make live` also refuses when :8090 is held by another program; `make setup` says clearly when Python 3.12 is missing. Tests with a fake PATH | `tools/doctor.sh`, `Makefile`, `demo/run.sh`, `demo/live_up.sh`, `tests/test_doctor.py` |
| 2 | Portability sweep (`git grep` for /home/<user>, ~/skypiea-*, Windows paths): no hard-coded user path in Makefile, mk/, demo/*.sh, tools/, infra/, .github/. `demo/screenshot.sh` keeps its optional WSL Chrome fallback (detected, not a user path). Paths in other owners' files: REQUESTS_W2 R1-R4. Dated handoff notes in docs/handoff/ are history and left unchanged | `docs/handoff/REQUESTS_W2.md` |
