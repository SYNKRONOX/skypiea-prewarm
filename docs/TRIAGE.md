# Triage: red-team issues, 8 to 15 November 2026

How the team handles issues opened during the red-team week. Goal: every issue gets a first
answer within one day, a reproduction attempt, one severity, and a closing note that says
what changed (commit) or why nothing did. Known gaps are in [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md);
vulnerabilities follow [`../SECURITY.md`](../SECURITY.md), never this public process.

## 1. First pass (same day)

1. **Security?** Credentials, a way to reach the console or the AWS node, unsafe download →
   ask the reporter to move it to a private report (SECURITY.md), hide details if needed,
   label `security`. Stop here.
2. **Known?** If it matches a row of `KNOWN_ISSUES.md` or README "Known limitations", answer
   with that row (template B), label `known-issue`, close. If the workaround did not work,
   keep it open: that is a new bug.
3. **Complete?** It needs the `make report` output (or `make doctor` + commit), the exact
   commands and the last lines of output. Missing → template C, label `needs-repro`.
4. Otherwise: one **area**, one **tier**, one **severity** label (below), and assign one person.

## 2. Reproduction checklist

Reproduce on a **fresh clone of the reported commit**, never in your working checkout:

```bash
git clone https://github.com/SYNKRONOX/skypiea-prewarm.git "/tmp/triage <issue no>"   # path with a space on purpose
cd "/tmp/triage <issue no>" && git checkout <commit from the report>
make report                       # compare with the reporter's: OS, Python, Docker, versions
make review MODE=test             # Tier 0 (about 5 min)
make review                       # Tier 0 + Tier 1 on its own cluster skypiea-review (deleted after)
```

- [ ] Same commit as the reporter? (`make report` first line)
- [ ] Same tier and same commands, copied literally from the issue.
- [ ] Platform: we can only reproduce on Linux x86-64 / WSL2. For macOS or arm64, say so and
      ask for the full output (template D).
- [ ] Python 3.12 (`make doctor DOCTOR_MODE=test`)? Docker running with >= 6 GB?
- [ ] Ports or a cluster from another run in the way? (`make doctor`; `kind get clusters`)
- [ ] For a **claim or number**: find the number's source in `docs/numbers_and_sources.md`
      and the committed file; run `make check-claims`. A number with no committed source is
      a bug in the docs, severity S2.
- [ ] Record the result in the issue: "reproduced at <commit> on <platform>" or
      "not reproduced at <commit> on <platform>, steps: ...".

## 3. Labels

Create these once in the GitHub repository (Issues → Labels); the bug template already adds `bug`.

| Label | Use for |
|---|---|
| `bug` | A command, target or test does something other than the docs say |
| `docs` | Wrong, confusing or broken docs, link or path, without a code fault |
| `claim` | A number or claim that does not match a committed file, or overstates it |
| `security` | Moved to a private report (see SECURITY.md); no details in public |
| `known-issue` | Already in `KNOWN_ISSUES.md` / README Known limitations |
| `needs-repro` | Waiting for the reporter's `make report` or exact steps |
| `cannot-reproduce` | Tried on a fresh clone of the same commit; did not happen |
| `platform` | macOS, arm64, other distributions: untested by us |
| `tier-0` ... `tier-3`, `console` | Where it happened |
| `S1` ... `S4` | Severity (below) |
| `wontfix` | Out of scope for the course project; the reason is in the closing note |

## 4. Severity

| Severity | Meaning | Examples | Target |
|---|---|---|---|
| **S1** | Security problem, or a headline claim is false | a secret in the repo; the console reachable from the network; a README result that a committed file contradicts | fix or retract the same day |
| **S2** | A tier does not work as documented on a tested platform, or a number has no committed source | `make demo` fails on a clean Ubuntu with Docker; a doc quotes a number no file contains | fix within 2 days |
| **S3** | Works, but a step is confusing, a message is unclear, a link or path is wrong | doctor says FAIL without a fix line; dead link | fix during the week |
| **S4** | Cosmetic, or an untested platform | wording; macOS gap | when time allows, or document |

Overclaiming counts as S1 or S2, never S4: when in doubt, weaken the claim and cite the file.

## 5. Response templates

**A. Acknowledged (first answer)**

> Thanks for testing Skypiea. We could reproduce this at `<commit>` on `<platform>`
> (`make report` attached). Labelled `<area>`, `<severity>`. `<name>` is on it; we will
> link the fix commit here.

**B. Known issue**

> Thanks. This is a known limitation: `docs/KNOWN_ISSUES.md`, "<row>". The workaround is:
> `<workaround>`. If it does not work for you, reopen with the output of `make report`.

**C. Need more**

> Thanks. To reproduce this we need: the output of `make report`, the exact commands from a
> fresh clone, and the last 30-50 lines of output. Without them we will close this in 3 days
> as `needs-repro` (reopen any time).

**D. Untested platform**

> Thanks. We only tested Linux x86-64 and Windows + WSL2 (README "Supported platforms"), so we
> cannot reproduce on `<platform>`. We have added it to `docs/KNOWN_ISSUES.md`. A pull request
> or the full failing output is welcome.

**E. Fixed**

> Fixed in `<commit>` (`<one line of what changed>`). Verified on a fresh clone with
> `<command>` → `<expected last line>`. Closing; reopen if it still happens for you.

**F. Claim corrected**

> You are right: `<doc>` said `<old>`, but `<committed file>` shows `<value>`. Corrected in
> `<commit>`; `make check-claims` now checks it.

## 6. During the week

- One person reads new issues each morning and evening (UTC+5:30) and does the first pass.
- Every fix: on a branch, `make test lint check-sizes check-claims`, link the issue in the
  commit message; never push numbers that are not in a committed file.
- At the end (15 Nov): list every issue with its severity and outcome in a handoff note under
  `docs/handoff/`, and move anything still open into `KNOWN_ISSUES.md`.
