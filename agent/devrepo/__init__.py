"""Mock repository for the Live Console's code-fix demo ("Dev assistant"), W1 #14.

The repository (agent/devrepo/sortkit.json: a small Python package with one PLANTED bug in
merge_sort and a correct quick_sort and binary_search, plus tests) lives in the console as
data. A ``Session`` holds one conversation's copy; edits persist for the conversation and
``reset()`` restores it.

NOTHING IS EXECUTED. ``run_tests`` is a SIMULATED test runner (labelled so everywhere):
- it parses the mock files with ``ast.parse`` (parsing is not running);
- for each ``test_*`` function it evaluates only asserts of the literal form
  ``assert f(<literal>, ...) == <literal>`` (also ``!=``, ``is None``) where f is merge_sort,
  quick_sort or binary_search, using ``ast.literal_eval`` for the arguments and the runner's
  OWN trusted reference behaviour for f;
- the reference behaviour of merge_sort follows the current source: while ``_merge`` drops
  the right-hand tail (the planted bug: no ``right[j:]`` / loop over ``right`` after the main
  loop) it behaves like the buggy code, after a fix like a correct merge sort; if the code
  changed in a way the runner cannot judge (merge_sort no longer uses ``_merge``), its
  merge_sort asserts are "not evaluated". quick_sort and
  binary_search keep their (correct) reference behaviour; a changed function body is
  reported as such, not run;
- a test with an assert it cannot evaluate is "not evaluated" (never a pass); a test with no
  assert fails; a syntax error in any file is a collection error.
"""

from __future__ import annotations

import ast
import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_PATH = Path(__file__).with_name("sortkit.json")
SIMULATED = "simulated test runner: the mock files are parsed, never executed"


@lru_cache(maxsize=1)
def pristine() -> dict:
    return json.loads(REPO_PATH.read_text())


# ---------------------------------------------------------------- reference behaviour
def _merge_buggy(left: list, right: list) -> list:
    out, i, j = [], 0, 0
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            out.append(left[i])
            i += 1
        else:
            out.append(right[j])
            j += 1
    return out + left[i:]                      # the planted bug: right[j:] is lost


def _merge_sort_buggy(xs: list) -> list:
    if len(xs) <= 1:
        return list(xs)
    m = len(xs) // 2
    return _merge_buggy(_merge_sort_buggy(xs[:m]), _merge_sort_buggy(xs[m:]))


def _binary_search(xs: list, t: Any) -> int:
    lo, hi = 0, len(xs) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if xs[mid] == t:
            return mid
        if xs[mid] < t:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1


def merge_state(source: str) -> str:
    """"fixed" | "buggy" | "unknown". buggy = the planted structure is still there:
    merge_sort calls _merge, and _merge keeps no right-hand tail after its main loop.
    unknown = the code changed in a way this simulated runner cannot judge (e.g. merge_sort no
    longer uses _merge): its tests are then "not evaluated", never failed or passed."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "unknown"
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    ms, mg = fns.get("merge_sort"), fns.get("_merge")
    if ms is None or mg is None:
        return "unknown"
    calls = {n.func.id for n in ast.walk(ms) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name)}
    if "_merge" not in calls or not any(isinstance(st, ast.While) for st in mg.body):
        return "unknown"
    return "fixed" if merge_is_fixed(source) else "buggy"


def merge_is_fixed(source: str) -> bool:
    """True if sorting._merge keeps the right-hand tail after its main loop."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
               and n.name == "_merge"), None)
    if fn is None:
        return False
    loops = [k for k, st in enumerate(fn.body) if isinstance(st, ast.While)]
    if not loops:
        return False
    for st in fn.body[loops[0] + 1:]:
        for n in ast.walk(st):
            if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and \
                    n.value.id == "right" and isinstance(n.slice, ast.Slice):
                return True
            if isinstance(n, ast.While) and "right" in ast.unparse(n.test):
                return True
    return False


class Session:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        repo = pristine()
        self.name = repo["name"]
        self.files: dict[str, str] = copy.deepcopy(repo["files"])
        self.edits: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []
        self.sidebar_due = False
        self.verified = None                     # (interface shared with Skypiea Air)

    # ---------------- tools ----------------
    def search(self, query: str = "") -> dict:
        q = str(query or "").strip()
        if not q:
            return {"error": "empty_query", "message": "Give a text to search for."}
        hits = [{"path": p, "line": i + 1, "text": ln.strip()[:120]}
                for p, src in sorted(self.files.items())
                for i, ln in enumerate(src.splitlines()) if q.lower() in ln.lower()]
        return {"query": q, "matches": hits[:20], "total": len(hits),
                **({} if hits else {"message": "Not found in this repository.",
                                    "files": sorted(self.files)})}

    def read_file(self, path: str = "", start_line: int | None = None,
                  end_line: int | None = None) -> dict:
        p = str(path or "").strip().lstrip("./")
        if p not in self.files:
            return {"error": "not_found", "message": f"No file {path!r} in this repository.",
                    "files": sorted(self.files)}
        lines = self.files[p].splitlines()
        a = max(1, int(start_line or 1))
        b = min(len(lines), int(end_line or len(lines)))
        return {"path": p, "start_line": a, "end_line": b, "total_lines": len(lines),
                "content": "\n".join(f"{i:>3}  {lines[i - 1]}" for i in range(a, b + 1))}

    def edit_file(self, path: str = "", old: str = "", new: str = "") -> dict:
        p = str(path or "").strip().lstrip("./")
        if p not in self.files and old:
            return {"error": "not_found", "message": f"No file {path!r}; nothing changed.",
                    "files": sorted(self.files)}
        src = self.files.get(p, "")
        if old:
            n = src.count(old)
            if n != 1:
                return {"error": "old_not_unique" if n else "old_not_found", "changed": False,
                        "message": f"`old` occurs {n} times in {p}; it must occur exactly "
                                   "once. Nothing changed. Read the file and copy the exact "
                                   "text."}
            out = src.replace(old, new, 1)
        else:
            if p in self.files:     # no `old`: append to an existing file
                out = src.rstrip("\n") + "\n\n\n" + new.strip("\n") + "\n"
            else:                   # new file
                out = new
        self.files[p] = out
        summary = f"-{len(old.splitlines()) if old else 0} +{len(new.splitlines())} lines"
        self.edits.append({"path": p, "summary": summary})
        self.sidebar_due = True
        return {"changed": True, "path": p, "summary": summary}

    def run_tests(self, selector: str = "") -> dict:
        res = simulate(self.files, str(selector or ""))
        self.runs.append({"passed": res["passed"], "failed": res["failed"],
                          "not_evaluated": res["not_evaluated"], "selector": selector})
        self.sidebar_due = True
        return res

    def call(self, tool: str, args: dict[str, Any]) -> dict:
        fn = getattr(self, tool, None)
        if tool not in ("search", "read_file", "edit_file", "run_tests") or not callable(fn):
            return {"error": "unknown_tool"}
        try:
            return fn(**args)
        except (TypeError, ValueError) as e:
            return {"error": "bad_arguments", "message": str(e)[:200]}

    def sidebar(self) -> str | None:
        return None

    def state(self) -> dict[str, Any]:
        base = pristine()["files"]
        changed = {}
        for e in self.edits:
            changed[e["path"]] = changed.get(e["path"], 0) + 1
        return {"name": self.name, "files": sorted(self.files),
                "changed": [{"path": p, "edits": n, "new_file": p not in base}
                            for p, n in sorted(changed.items())],
                "edits": self.edits[-8:], "runs": self.runs[-6:],
                "merge_sort_fixed": merge_is_fixed(self.files.get("sortkit/sorting.py", "")),
                "merge_sort_state": merge_state(self.files.get("sortkit/sorting.py", "")),
                "diff": self.diff(), "runner": SIMULATED}

    def diff(self, max_lines: int = 60) -> list[str]:
        """Unified diff of the changed files against the pristine repository."""
        import difflib
        base = pristine()["files"]
        out: list[str] = []
        for p in sorted(self.files):
            if self.files[p] != base.get(p):
                out += list(difflib.unified_diff(
                    base.get(p, "").splitlines(), self.files[p].splitlines(),
                    f"a/{p}", f"b/{p}", n=1, lineterm=""))
        return out[:max_lines]


# ---------------------------------------------------------------- the simulated runner
_FUNCS = ("merge_sort", "quick_sort", "binary_search")


def simulate(files: dict[str, str], selector: str = "") -> dict[str, Any]:
    trees = {}
    for p, src in sorted(files.items()):
        if not p.endswith(".py"):
            continue
        try:
            trees[p] = ast.parse(src)
        except SyntaxError as e:
            return {"runner": SIMULATED, "collection_error": f"SyntaxError in {p} line "
                    f"{e.lineno}: {e.msg}", "passed": 0, "failed": 0, "not_evaluated": 0,
                    "tests": []}
    mstate = merge_state(files.get("sortkit/sorting.py", ""))
    pristine_files = pristine()["files"]
    changed_impl = sorted(p for p in ("sortkit/search.py",)
                          if files.get(p) != pristine_files.get(p))
    impl = {"merge_sort": {"fixed": sorted, "buggy": _merge_sort_buggy}.get(mstate),
            "quick_sort": sorted, "binary_search": _binary_search}
    tests = []
    for p, tree in trees.items():
        if not (p.startswith("tests/") and p.rsplit("/", 1)[-1].startswith("test_")):
            continue
        for fn in tree.body:
            if not isinstance(fn, ast.FunctionDef) or not fn.name.startswith("test_"):
                continue
            nid = f"{p}::{fn.name}"
            if selector and selector not in nid:
                continue
            tests.append({"id": nid, **_evaluate(fn, impl)})
    out = {"runner": SIMULATED, "tests": tests,
           "passed": sum(t["outcome"] == "passed" for t in tests),
           "failed": sum(t["outcome"] == "failed" for t in tests),
           "not_evaluated": sum(t["outcome"] == "not evaluated" for t in tests)}
    if mstate == "unknown":
        out["note_merge_sort"] = ("sortkit/sorting.py changed in a way the simulated runner "
                                  "cannot judge (merge_sort no longer uses the original _merge "
                                  "loop): merge_sort asserts are not evaluated")
    if changed_impl:
        out["note"] = (f"{', '.join(changed_impl)} changed: the simulated runner keeps its "
                       "reference behaviour for binary_search (code is never run)")
    return out


def _evaluate(fn: ast.FunctionDef, impl: dict) -> dict[str, Any]:
    asserts = [n for n in ast.walk(fn) if isinstance(n, ast.Assert)]
    if not asserts:
        return {"outcome": "failed", "detail": "no assert"}
    for a in asserts:
        got = _check(a.test, impl)
        if got is None:
            return {"outcome": "not evaluated",
                    "detail": f"cannot evaluate `{ast.unparse(a.test)[:80]}` (the simulated "
                              "runner supports literal asserts on merge_sort, quick_sort, "
                              "binary_search)"}
        ok, msg = got
        if not ok:
            return {"outcome": "failed", "detail": msg}
    return {"outcome": "passed"}


def _check(t: ast.expr, impl: dict) -> tuple[bool, str] | None:
    if not (isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.left, ast.Call)):
        return None
    call = t.left
    if not (isinstance(call.func, ast.Name) and call.func.id in _FUNCS) or call.keywords:
        return None
    try:
        args = [ast.literal_eval(x) for x in call.args]
        want = ast.literal_eval(t.comparators[0])
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None
    if impl.get(call.func.id) is None:          # behaviour unknown: not evaluated
        return None
    try:
        got = impl[call.func.id](*args)
    except Exception:  # noqa: BLE001 - the reference raised on these literals
        return None
    op = t.ops[0]
    if isinstance(op, ast.Eq):
        ok = got == want
    elif isinstance(op, ast.NotEq):
        ok = got != want
    elif isinstance(op, ast.Is):
        ok = got is want
    else:
        return None
    return ok, "" if ok else f"{ast.unparse(call)} returned {got!r}, expected {want!r}"
