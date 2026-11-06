"""Code-fix demo (W1 #14-15): mock sortkit repository, edits persist per session, and the
SIMULATED test runner (parses, never executes; literal asserts only; honest outcomes)."""

from __future__ import annotations

import asyncio

import pytest

from agent.devrepo import SIMULATED, Session, merge_is_fixed, simulate

FIX_OLD = "    result.extend(left[i:])\n"
FIX_NEW = "    result.extend(left[i:])\n    result.extend(right[j:])\n"


def test_planted_bug_fails_merge_sort_tests_only() -> None:
    s = Session()
    r = s.run_tests()
    assert r["runner"] == SIMULATED
    out = {t["id"].split("::")[1]: t["outcome"] for t in r["tests"]}
    assert out["test_merge_sort_basic"] == "failed"
    assert out["test_merge_sort_duplicates"] == "failed"
    assert out["test_merge_sort_empty_and_single"] == "passed"
    assert out["test_quick_sort_basic"] == "passed"
    assert out["test_binary_search_finds_middle"] == "passed"
    assert (r["passed"], r["failed"]) == (4, 2)
    bad = next(t for t in r["tests"] if t["id"].endswith("test_merge_sort_basic"))
    assert "returned [1, 3]" in bad["detail"]


def test_fix_turns_failed_into_passed_and_card_shows_it() -> None:
    s = Session()
    s.run_tests("test_sorting")
    assert s.edit_file("sortkit/sorting.py", "nope", "x")["error"] == "old_not_found"
    assert s.edit_file("sortkit/sorting.py", "        return list(items)\n", "x")[
        "error"] == "old_not_unique"
    assert s.edit_file("sortkit/sorting.py", FIX_OLD, FIX_NEW)["changed"]
    r = s.run_tests("test_sorting")
    assert (r["passed"], r["failed"]) == (5, 0)
    st = s.state()
    assert st["merge_sort_fixed"] and st["changed"] == [{"path": "sortkit/sorting.py",
                                                         "edits": 1, "new_file": False}]
    assert [(x["passed"], x["failed"]) for x in st["runs"]] == [(3, 2), (5, 0)]
    s.reset()
    assert not s.edits and not merge_is_fixed(s.files["sortkit/sorting.py"])


@pytest.mark.parametrize("fix", [
    "    return result + left[i:] + right[j:]\n",
    "    while j < len(right):\n        result.append(right[j])\n        j += 1\n"
    "    result.extend(left[i:])\n"])
def test_other_correct_fixes_are_recognised(fix) -> None:
    s = Session()
    src = s.files["sortkit/sorting.py"].replace(FIX_OLD + "    return result\n", fix)
    if "return" not in fix:
        src = src.replace(fix, fix + "    return result\n")
    s.files["sortkit/sorting.py"] = src
    assert merge_is_fixed(src)
    assert s.run_tests("merge")["failed"] == 0


def test_new_tests_are_evaluated_literally_and_never_faked() -> None:
    s = Session()
    s.edit_file("tests/test_search.py", "", "def test_missing():\n"
                "    assert binary_search([1, 2, 3], 4) == -1\n\n\n"
                "def test_wrong():\n    assert binary_search([1, 2, 3], 1) == 2\n\n\n"
                "def test_cannot_tell():\n    xs = list(range(5))\n"
                "    assert binary_search(xs, 4) == 4\n\n\n"
                "def test_no_assert():\n    binary_search([1], 1)\n")
    r = {t["id"].split("::")[1]: t["outcome"] for t in s.run_tests("search")["tests"]}
    assert r == {"test_binary_search_finds_middle": "passed", "test_missing": "passed",
                 "test_wrong": "failed", "test_cannot_tell": "not evaluated",
                 "test_no_assert": "failed"}


def test_syntax_error_is_a_collection_error_and_nothing_runs(monkeypatch) -> None:
    import builtins
    s = Session()
    s.edit_file("sortkit/sorting.py", FIX_OLD, "    result.extend(left[i:]\n")
    called = []
    monkeypatch.setattr(builtins, "exec", lambda *a, **k: called.append(a))
    r = simulate(s.files)
    assert "SyntaxError in sortkit/sorting.py" in r["collection_error"] and not called


def test_search_read_and_out_of_repo() -> None:
    s = Session()
    hits = s.search("def merge_sort")["matches"]
    assert hits[0]["path"] == "sortkit/sorting.py"
    assert s.search("weather")["message"] == "Not found in this repository."
    assert s.read_file("nope.py")["error"] == "not_found"
    assert "def binary_search" in s.read_file("./sortkit/search.py")["content"]
    assert s.call("shell", {"command": "rm -rf /"})["error"] == "unknown_tool"


def test_codefix_in_the_engine_uses_the_repo_and_sends_no_content() -> None:
    pytest.importorskip("starlette")
    import json

    from agent.tasks import get
    from console import truth
    from console.engine import Conversation
    from console.llm_stream import ScriptedStreamingLLM
    from tests.test_console import FakeGW, FakeStack, NullPoller, factory

    task = get("codefix")
    gws = {"base": FakeGW("base"), "pred": FakeGW("pred")}

    async def publish(ev):
        pass
    conv = Conversation(task, ScriptedStreamingLLM([task.scripted(0)]),
                        {"base": FakeStack("base"), "pred": FakeStack("pred")}, NullPoller(),
                        publish, {"mirror": "concurrent", "hint_mode": "off",
                                  "baseline_policy": "scale_to_zero"}, mcp_factory=factory(gws))
    asyncio.run(conv.user_turn("fix it"))
    assert [x[1:] for x in gws["pred"].seen] == [("search", {}), ("read_file", {}),
                                                 ("write_file", {}), ("run_tests", {})]
    last = json.loads([m for m in conv.messages if m["role"] == "tool"][-1]["content"])
    assert last["failed"] == 0 and last["runner"] == SIMULATED
    st = truth.state(conv.backend)
    assert st["repo"]["merge_sort_fixed"] and conv.truth == []
    assert "result.extend" not in json.dumps(conv.calls)
