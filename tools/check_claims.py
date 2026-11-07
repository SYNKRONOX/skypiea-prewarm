"""Claim-vs-evidence checker (`make check-claims`). Offline, read-only, deterministic.

Checks:
  (a) numbers: every table row and headline number in README.md and docs/PRD_STATUS.md that is
      listed below against the committed result file it is copied from (recomputed from the
      CSV/JSON where the doc formats it itself, else the row must appear in the generated file);
  (b) links: every relative link in every tracked *.md resolves to a tracked file or folder
      (and, for a link to a heading of a Markdown file, the heading exists);
  (c) make targets: every `make X` written in code in a tracked *.md names a real target of
      the Makefile or mk/*.mk;
  (d) paths: every path written in code in dev.md and docs/REVIEWER_GUIDE.md exists;
  (e) commands: every command line in README.md and docs/REVIEWER_GUIDE.md code blocks parses:
      make targets exist, `bin/skypiea <cmd>` answers `--help`, `python -m <module>` resolves,
      `./script` / `tools/x.sh` paths exist and pass `bash -n`.

    python tools/check_claims.py            # all checks; exit 1 on any mismatch
    python tools/check_claims.py --only links,make
"""

from __future__ import annotations

import argparse
import csv
import gzip
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Old handoff logs are kept as recorded (dated working notes); their links are not checked.
LINK_SKIP = {"docs/handoff/phase2_model.txt"}
CMD_DOCS = ["README.md", "docs/REVIEWER_GUIDE.md"]
PATH_DOCS = ["dev.md", "docs/REVIEWER_GUIDE.md"]


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.counts: dict[str, int] = {}

    def ok(self, check: str) -> None:
        self.counts[check] = self.counts.get(check, 0) + 1

    def bad(self, check: str, msg: str) -> None:
        self.errors.append(f"[{check}] {msg}")


# ------------------------------------------------------------------------------------ helpers
def tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.splitlines()


def read_csv(rel: str) -> list[dict[str, str]]:
    p = ROOT / rel
    raw = gzip.open(p, "rt").read() if rel.endswith(".gz") else p.read_text()
    return list(csv.DictReader(io.StringIO(raw)))


def code_spans(md: str) -> tuple[list[str], list[str]]:
    """(inline code spans outside fences, lines inside fenced code blocks)."""
    spans, fenced, in_fence = [], [], False
    for line in md.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            fenced.append(line)
        else:
            spans += re.findall(r"`([^`\n]+)`", line)
    return spans, fenced


def strip_fences(md: str) -> str:
    out, in_fence = [], False
    for line in md.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append("")
            continue
        out.append("" if in_fence else re.sub(r"`[^`\n]*`", "``", line))
    return "\n".join(out)


def slug(heading: str) -> str:
    """GitHub's heading anchor."""
    h = re.sub(r"<[^>]+>", "", heading).strip().lower()
    h = re.sub(r"[`*_\[\]()]", lambda m: "" if m.group() != "_" else "_", h)
    h = re.sub(r"\]\([^)]*\)", "", h)
    h = re.sub(r"[^\w\- ]", "", h, flags=re.UNICODE)
    return h.replace(" ", "-")


def anchors(md: str) -> set[str]:
    seen: dict[str, int] = {}
    out = set()
    for line in strip_fences(md).splitlines():
        m = re.match(r"#{1,6}\s+(.*)", line)
        if not m:
            continue
        s = slug(m.group(1).replace("``", ""))
        # the fenced/inline code was blanked; recompute from the raw heading
        out.add(s)
        n = seen.get(s, 0)
        if n:
            out.add(f"{s}-{n}")
        seen[s] = n + 1
    for line in md.splitlines():
        m = re.match(r"#{1,6}\s+(.*)", line)
        if m:
            out.add(slug(m.group(1)))
    out.update(re.findall(r'<a\s+(?:id|name)="([^"]+)"', md))
    return out


def cells(row: str) -> list[str]:
    c = [x.strip() for x in row.strip().strip("|").split("|")]
    return [re.sub(r"\*\*|†", "", x).strip() for x in c]


def table_rows(md: str, heading: str) -> list[str]:
    """Data rows of the first table after the heading line that starts with `heading`."""
    lines = md.splitlines()
    i = next(n for n, line in enumerate(lines) if line.startswith(heading))
    rows, started = [], False
    for line in lines[i + 1 :]:
        if line.startswith("#"):
            break
        if line.startswith("|"):
            started = True
            if not line.startswith("|---"):
                rows.append(line)
        elif started:
            break
    return rows[1:]  # drop the header


def fmt_int(x: float) -> str:
    return f"{round(x):,}"


# ------------------------------------------------------------------------------- (a) numbers
def check_numbers(rep: Report) -> None:
    readme = (ROOT / "README.md").read_text()
    status = (ROOT / "docs/PRD_STATUS.md").read_text()
    C = "numbers"

    def need(doc: str, name: str, text: str, where: str) -> None:
        if text in doc:
            rep.ok(C)
        else:
            rep.bad(C, f"{where}: expected '{text}' (from {name})")

    # Tables that are copied verbatim from a generated file
    verbatim = {
        "### Simulator: equal-idle margins": "docs/results_summary.md",
        "### Oracle: the remaining headroom": "docs/results_summary.md",
        "### Next-tool accuracy (E1, TEST)": "docs/results_summary.md",
        "### Predictor service latency": "docs/results_summary.md",
    }
    for heading, src in verbatim.items():
        lines = {line.strip() for line in (ROOT / src).read_text().splitlines()}
        for row in table_rows(readme, heading):
            if row.strip() in lines:
                rep.ok(C)
            else:
                rep.bad(C, f"README '{heading[4:]}': row not in {src}: {row[:80]}")

    # Tables whose cells are a subsequence of a source row (the source has more columns)
    subseq = {
        "#### Live Console verification runs": "results/live_console_demo/runs.md",
    }
    for heading, src in subseq.items():
        src_rows = [
            cells(x)
            for x in (ROOT / src).read_text().splitlines()
            if x.startswith("|") and not x.startswith("|---")
        ]
        for row in table_rows(readme, heading):
            want = cells(row)
            if any(_is_subseq(want, s) for s in src_rows):
                rep.ok(C)
            else:
                rep.bad(C, f"README '{heading[5:]}': row not found in {src}: {row[:80]}")

    # Lead-time grid: README "demo | lookahead | hint" -> section "## demo", hint label mapped
    lt_src: dict[tuple[str, str, str], list[str]] = {}
    sect = ""
    for line in (ROOT / "results/live_console_leadtime/tables.md").read_text().splitlines():
        if line.startswith("## "):
            sect = line[3:].strip()
        elif line.startswith("| ") and sect and not line.startswith("| lookahead"):
            c = cells(line)
            if len(c) == 7 and (sect, c[0], c[1]) not in lt_src:
                lt_src[sect, c[0], c[1]] = c
    hint = {"off": "hint off", "Enter": "hint at Enter", "typing": "hint at typing start"}
    for row in table_rows(readme, "#### Lead-time grid"):
        c = cells(row)
        s_ = lt_src.get((c[0], c[1], hint.get(c[2], "?")))
        if s_ and s_[3:6] == c[3:6]:
            rep.ok(C)
        else:
            rep.bad(
                C,
                f"README lead-time row {c[:3]}: {c[3:6]} != {s_[3:6] if s_ else None} "
                "(results/live_console_leadtime/tables.md)",
            )

    # E0 table, recomputed from the summary CSVs
    def by(rel: str) -> dict[tuple[str, str], dict[str, str]]:
        return {(r["tool"], r["mode"]): r for r in read_csv(rel)}

    aws, p3, kind = (
        by("results/e0_aws_summary.csv"),
        by("results/e0_aws_p3_summary.csv"),
        by("results/e0_kind_summary.csv"),
    )
    for row in table_rows(readme, "### E0: cold starts per tool"):
        c = cells(row)
        t = c[0]

        def ms(src: dict, mode: str, col: str = "total_median_ms", t: str = t) -> str:
            return str(round(float(src[t, mode][col])))

        want = [
            t,
            aws[t, "cached"]["tier"],
            f"{ms(aws, 'cached')} ({ms(aws, 'cached', 'total_p95_ms')})",
            f"{ms(aws, 'uncached')} ({ms(aws, 'uncached', 'total_p95_ms')})",
            ms(p3, "cached"),
            ms(kind, "cached"),
            ms(kind, "uncached"),
        ]
        if c == want:
            rep.ok(C)
        else:
            rep.bad(C, f"README E0 row {t}: {c} != {want} (results/e0_*_summary.csv)")
    lo = min(round(float(r["total_median_ms"])) for (_, m), r in aws.items() if m == "cached")
    hi = max(round(float(r["total_median_ms"])) for (_, m), r in aws.items() if m == "uncached")
    need(readme, "results/e0_aws_summary.csv", f"median {lo:,} to {hi:,} ms", "README intro")

    # AWS scenario tables, recomputed from the summary CSVs (CI: see docs/results_summary.md)
    label = {
        "always_warm": "always-warm",
        "keep_alive": "keep-alive 30 s",
        "keep_alive_10": "keep-alive 10 s",
        "keep_alive_60": "keep-alive 60 s",
        "markov1": "Markov-1 top-1",
        "predictive": "predictive (Skypiea)",
        "scale_to_zero": "scale-to-zero",
    }
    for heading, src in [
        ("### Real AWS scenario runs", "results/scenario_aws_summary.csv"),
        ("### SYNTHETIC: PRD scenario runs", "results/scenario_aws_prd_SYNTHETIC_summary.csv"),
    ]:
        rows = read_csv(src)
        for row in table_rows(readme, heading):
            c = cells(row)
            plat = c[7]
            cand = [
                r
                for r in rows
                if r["scenario"] == c[0]
                and r["platform_image"].endswith(plat)
                and c[1].startswith(label.get(r["policy"], "?"))
            ]
            ok = False
            for r in cand:
                if not r["cold_rate"]:  # a failed run (error column set); not in the table
                    continue
                got = [
                    r["calls"],
                    f"{100 * int(r['cold']) / int(r['calls']):.1f}",
                    fmt_int(float(r["idle_pod_s"])),
                    f"{round(float(r['added_ms_p50']))}",
                    f"{round(float(r['added_ms_p99']))}",
                ]
                have = [c[2], c[3].split(" [")[0], c[4], c[5], c[6]]
                if got == have:
                    ok = True
            if ok:
                rep.ok(C)
            else:
                rep.bad(C, f"README '{heading[4:]}' row {c[:2]} {plat}: no matching row in {src}")

    # Agent runs table, from results/agent_runs/summary.csv
    pol = {
        "scale-to-zero": "scale_to_zero",
        "keep-alive 30 s": "keep_alive",
        "predictive (Skypiea)": "predictive",
    }
    agent = {(r["scenario"], r["policy"]): r for r in read_csv("results/agent_runs/summary.csv")}
    for row in table_rows(readme, "#### Live LLM agent through the gateway"):
        c = cells(row)
        r = agent.get((c[0], pol.get(c[1], "?")))
        want = (
            None
            if r is None
            else [
                r["sessions"],
                r["ok_calls"],
                r["cold_rate_pct"],
                fmt_int(float(r["added_p50_ms"])),
                fmt_int(float(r["added_p99_ms"])),
                fmt_int(float(r["idle_pod_s"])),
                f"{r['followed_graph']}/{r['sessions']}",
            ]
        )
        if want == c[2:]:
            rep.ok(C)
        else:
            rep.bad(
                C, f"README agent row {c[:2]}: {c[2:]} != {want} (results/agent_runs/summary.csv)"
            )

    # Skye correctness suite (results/suite/*/summary.md)
    after = (ROOT / "results/suite/after/summary.md").read_text()
    m = re.search(r"\| \*\*all\*\* \| (\d+/\d+) \| (\d+/\d+) \| (\d+/\d+) \|", after)
    hdr = re.search(r"\| Scenario \| (.+?) \| (.+?) \| (.+?) \|", after)
    suite = (
        dict(zip([h.strip() for h in hdr.groups()], m.groups(), strict=True)) if m and hdr else {}
    )
    before = (ROOT / "docs/SUITE_BEFORE_AFTER.md").read_text()
    b = re.search(r"\| \*\*all\*\* \| (\d+/\d+) \|", before)
    llama, qwen3, qwen25 = suite.get("llama3.1:8b"), suite.get("qwen3:8b"), suite.get("qwen2.5:7b")
    need(
        readme,
        "results/suite/after/summary.md",
        f"passed\n  {llama} runs with llama3.1:8b after the guardrails "
        f"({b.group(1) if b else '?'} before), "
        f"{qwen3} with qwen3:8b and {qwen25}\n  with qwen2.5:7b",
        "README Tier 2 suite bullet",
    )
    need(
        readme,
        "results/suite/after/summary.md",
        f"Skye's {llama} suite result",
        "README Known limitations",
    )

    # Demo world (generated from a fixed seed; agent/skyair/world.py)
    try:
        from agent.skyair import world as W

        ws = W.summary()
    except Exception as e:  # pragma: no cover - reported, not raised
        rep.bad(C, f"agent.skyair.world could not be built: {e}")
        ws = None
    if ws is not None:
        need(
            readme,
            "agent/skyair/world.py",
            f"{ws['airports']} real airport codes ({ws['indian']} in India, "
            f"{ws['airports'] - ws['indian']}\n  abroad)",
            "README demo world",
        )
        need(
            readme,
            "agent/skyair/world.py",
            f"{ws['customers']} fictional customers, {ws['bookings']} bookings",
            "README demo world",
        )
        d = W.ANCHOR
        need(
            readme,
            "agent/skyair/world.py",
            f"**{d.strftime('%A')} {d.day} {d.strftime('%B %Y')}**",
            "README demo world date",
        )

    # PRD_STATUS headline numbers
    lt = [
        r for r in read_csv("results/loadtest.csv") if r["mode"] == "open" and r["workers"] == "1"
    ]
    p99 = {r["rate"]: f"{float(r['p99_ms']):.1f}" for r in lt}
    need(
        status,
        "results/loadtest.csv",
        f"p99 {p99.get('75.0')} ms at 75 req/s, {p99.get('150.0')} ms at 150 req/s",
        "PRD_STATUS FR-1",
    )
    fr6 = json.loads((ROOT / "results/fr6_reload_check.json").read_text())
    secs = [r["service_seconds"] for r in fr6["reloads"]]
    need(
        status,
        "results/fr6_reload_check.json",
        f"each reload took {min(secs):.2f}-{max(secs):.2f} s in the service",
        "PRD_STATUS FR-6",
    )
    need(
        status,
        "results/fr6_reload_check.json",
        f"0 non-200 of {fr6['requests']:,} requests",
        "PRD_STATUS FR-6",
    )
    c40 = [r for r in read_csv("results/concurrent40_aws.csv") if r["calls"]]
    need(
        status,
        "results/concurrent40_aws.csv",
        f"{c40[-1]['ok']} of {c40[-1]['calls']} calls succeeded",
        "PRD_STATUS NFR-4",
    )
    sa = {
        (r["scenario"], r["policy"]): float(r["idle_pod_s"])
        for r in read_csv("results/scenario_aws_summary.csv")
        if r["platform_image"].endswith("b652e6b") and r["idle_pod_s"]
    }
    ra = 100 * sa["A", "predictive"] / sa["A", "always_warm"]
    rb = 100 * sa["B", "predictive"] / sa["B", "always_warm"]
    need(
        status,
        "results/scenario_aws_summary.csv",
        f"predictive {ra:.1f}% / {rb:.1f}% of always-warm's",
        "PRD_STATUS NFR-3",
    )
    calls = read_csv("results/scenario_aws_calls.csv")
    held = [float(r["held_ms"]) for r in calls if r["held_ms"] not in ("", "0", "0.0")]
    over = sum(h > 150.5 for h in held)
    need(
        status,
        "results/scenario_aws_calls.csv",
        f"{len(held)} held calls, {over} above 150.5 ms",
        "PRD_STATUS FR-9",
    )


def _is_subseq(want: list[str], have: list[str]) -> bool:
    it = iter(have)
    return (
        bool(want)
        and want[0] == (have[0] if have else None)
        and all(any(w == h for h in it) for w in want)
    )


# --------------------------------------------------------------------------------- (b) links
def check_links(rep: Report, files: list[str]) -> None:
    tracked_set = set(files)
    dirs = {str(Path(f).parent) for f in files} | {str(p) for f in files for p in Path(f).parents}
    for md in [f for f in files if f.endswith(".md") and f not in LINK_SKIP]:
        text = strip_fences((ROOT / md).read_text())
        for target in re.findall(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", text):
            if re.match(r"[a-z]+:", target) or target.startswith("<"):
                continue
            path, _, frag = target.partition("#")
            if not path:  # same-file anchor
                dest = md
            else:
                dest = str((Path(md).parent / path).as_posix())
                dest = (
                    str(Path(dest).resolve().relative_to(ROOT))
                    if (ROOT / dest).exists()
                    else str(Path(dest)).replace("\\", "/")
                )
                dest = dest.rstrip("/")
                norm = str(Path(*[p for p in Path(dest).parts]))
                if norm not in tracked_set and norm not in dirs:
                    rep.bad("links", f"{md}: broken link ({target})")
                    continue
                dest = norm
            if frag and dest.endswith(".md") and dest in tracked_set:
                if frag.lower() not in anchors((ROOT / dest).read_text()):
                    rep.bad("links", f"{md}: no heading #{frag} in {dest}")
                    continue
            rep.ok("links")


# --------------------------------------------------------------------------- (c) make targets
_KNOWN: set[str] = set()


def known_targets() -> set[str]:
    if not _KNOWN:
        _KNOWN.update(make_targets())
    return _KNOWN


def make_targets() -> set[str]:
    out = set()
    for f in [ROOT / "Makefile", *sorted((ROOT / "mk").glob("*.mk"))]:
        for line in f.read_text().splitlines():
            m = re.match(
                r"^([a-zA-Z0-9][a-zA-Z0-9_.-]*(?:\s+[a-zA-Z0-9][a-zA-Z0-9_.-]*)*)\s*:(?!=)", line
            )
            if m:
                out.update(m.group(1).split())
    return out


def make_calls(text: str) -> Iterable[tuple[str, list[str]]]:
    """(the line, target names) for every `make ...` invocation in a code line or span."""
    text = re.sub(r"<[^>]*>", "X", text)  # placeholders such as <release asset URL>
    for m in re.finditer(r"(?:^|[\s;&|(`$])make((?:\s+[^\s;&|)#`]+)*)", text):
        args, targets = m.group(1).split(), []
        skip_next = False
        for a in args:
            if skip_next:
                skip_next = False
                continue
            if a in ("-f", "-C"):
                skip_next = True
                continue
            if a.startswith("-") or "=" in a:
                continue
            if a.endswith(":") or "..." in a or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*\*?", a):
                break  # an error message ("make setup: ...") or a placeholder
            if targets and not re.search(r"[-_*]", a) and a not in known_targets():
                break  # prose after a target ("make demo stopped before ...")
            targets.append(a)
        yield m.group(0).strip(), targets


def check_make(rep: Report, files: list[str]) -> None:
    known = make_targets()
    for md in [f for f in files if f.endswith(".md") and not f.startswith("docs/handoff/")]:
        spans, fenced = code_spans((ROOT / md).read_text())
        for chunk in spans + fenced:
            if "make" not in chunk:
                continue
            for line, targets in make_calls(chunk):
                for t in targets:
                    if t.endswith("*"):
                        hit = any(k.startswith(t[:-1]) for k in known)
                    else:
                        hit = t in known
                    if hit:
                        rep.ok("make")
                    else:
                        rep.bad("make", f"{md}: `{line}`: no make target '{t}'")


# --------------------------------------------------------------------------------- (d) paths
# Code spans with a slash that are not repository paths
PATH_NOT_FILES = {"tools/call", "tools/list", "SYNKRONOX/skypiea-prewarm"}
PATH_EXT = r"\.(py|md|yaml|yml|json|sh|csv|txt|gz|toml|lock|png|mk|html|js|css|log|tf)$"


def check_paths(rep: Report, files: list[str], docs: list[str]) -> None:
    tracked_set = set(files)
    dirs = {str(p) for f in files for p in Path(f).parents}
    basenames = {Path(f).name for f in files}
    dirnames = {p.name for f in files for p in Path(f).parents}  # "skyair/" next to "agent/"
    for md in docs:
        p = ROOT / md
        if not p.exists():
            continue
        spans, fenced = code_spans(p.read_text())
        for tok in spans:
            tok = tok.strip()
            if (
                " " in tok
                or ":" in tok
                or tok.startswith(("/", "~", "$", "-", "."))
                and not tok.startswith(".github")
                or "<" in tok
                or "{" in tok
                or "=" in tok
            ):
                continue
            if not ("/" in tok or re.search(PATH_EXT, tok)):
                continue
            if re.match(r"^[\w.]+$", tok) and "/" not in tok and not re.search(PATH_EXT, tok):
                continue
            t = tok.rstrip("/")
            if t in PATH_NOT_FILES:
                continue
            if "*" in t:
                ok = any(Path(f).match(t) for f in files) or bool(list(ROOT.glob(t)))
            elif "/" in tok:
                rel = os.path.normpath(str(Path(md).parent / t))  # relative to the doc
                ok = (
                    t in tracked_set
                    or t in dirs
                    or t in dirnames
                    or rel in tracked_set
                    or rel in dirs
                )
            else:
                ok = t in basenames
            if ok:
                rep.ok("paths")
            else:
                rep.bad("paths", f"{md}: path `{tok}` does not exist in the repository")


# ------------------------------------------------------------------------------ (e) commands
def command_lines(md: str) -> list[str]:
    out, in_fence, lang = [], False, ""
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("```"):
            in_fence, lang = (not in_fence), s[3:].strip()
            continue
        if in_fence and lang in ("bash", "sh", "shell", ""):
            cmd = re.split(r"\s+#\s", " " + s, maxsplit=1)[0].strip()
            if (
                cmd
                and not cmd.startswith("#")
                and not cmd.startswith("|")
                and not cmd.startswith("===")
            ):
                out.append(cmd)
    return out


def check_commands(rep: Report, docs: list[str]) -> None:
    known = make_targets()
    py = sys.executable
    help_cache: dict[str, bool] = {}
    for md in docs:
        p = ROOT / md
        if not p.exists():
            continue
        for cmd in command_lines(p.read_text()):
            for part in re.split(r"\s*(?:&&|\|\||;|\|)\s*", cmd):
                words = part.split()
                while words and re.fullmatch(r"[A-Z_][A-Z0-9_]*=\S*", words[0]):
                    words = words[1:]
                if not words:
                    continue
                w0 = words[0]
                if w0 == "make":
                    _, targets = next(iter(make_calls(" " + part)), ("", []))
                    bad = [t for t in targets if t not in known]
                    if bad:
                        rep.bad("commands", f"{md}: `{part}`: unknown make target {bad}")
                    else:
                        rep.ok("commands")
                elif w0 == "bin/skypiea":
                    sub = next(
                        (
                            w
                            for w in words[1:]
                            if not w.startswith("-")
                            and w not in ("kind-skypiea",)
                            and not w.startswith("~")
                            and words[words.index(w) - 1] not in ("--context", "--kubeconfig", "-n")
                        ),
                        "",
                    )
                    key = sub
                    if key not in help_cache:
                        r = subprocess.run(
                            [py, "-m", "skypiea_cli", *([sub] if sub else []), "--help"],
                            cwd=ROOT,
                            capture_output=True,
                            text=True,
                        )
                        if r.returncode != 0:  # the wrapper may not be a module; try the script
                            r = subprocess.run(
                                ["bash", "bin/skypiea", *([sub] if sub else []), "--help"],
                                cwd=ROOT,
                                capture_output=True,
                                text=True,
                            )
                        help_cache[key] = r.returncode == 0
                    if help_cache[key]:
                        rep.ok("commands")
                    else:
                        rep.bad("commands", f"{md}: `{part}`: bin/skypiea {sub} --help failed")
                elif w0 in ("python", "python3", ".venv/bin/python") and words[1:2] == ["-m"]:
                    mod = words[2] if len(words) > 2 else ""
                    if (
                        mod
                        and importlib.util.find_spec(mod.split(".")[0]) is not None
                        and _find(mod)
                    ):
                        rep.ok("commands")
                    else:
                        rep.bad("commands", f"{md}: `{part}`: module {mod} not found")
                elif "/" in w0 and not w0.startswith(("~", "/", "http")) and not w0.startswith("$"):
                    f = ROOT / w0
                    if not f.exists():
                        rep.bad("commands", f"{md}: `{part}`: {w0} does not exist")
                    elif f.suffix == ".sh" and subprocess.run(["bash", "-n", str(f)]).returncode:
                        rep.bad("commands", f"{md}: `{part}`: {w0} does not parse (bash -n)")
                    else:
                        rep.ok("commands")


def _find(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


# ------------------------------------------------------------------------------------- main
CHECKS: dict[str, Callable[[Report, list[str]], None]] = {
    "numbers": lambda r, f: check_numbers(r),
    "links": check_links,
    "make": check_make,
    "paths": lambda r, f: check_paths(r, f, PATH_DOCS),
    "commands": lambda r, f: check_commands(r, CMD_DOCS),
}


def run(only: list[str] | None = None) -> Report:
    rep = Report()
    files = tracked()
    for name, fn in CHECKS.items():
        if only and name not in only:
            continue
        fn(rep, files)
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", help="comma-separated subset: " + ",".join(CHECKS))
    a = ap.parse_args()
    rep = run(a.only.split(",") if a.only else None)
    for e in rep.errors:
        print(e)
    summary = ", ".join(f"{k} {v} ok" for k, v in rep.counts.items())
    print(f"check-claims: {summary}; {len(rep.errors)} mismatch(es)")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
