"""PRD Scenario B: coding PR review (read_file -> run_tests -> format_diff -> post_comment)."""

from __future__ import annotations

from typing import Any

from agent.tasks import FINISH_TOOL, Task, fn

SYSTEM = """You are a code-review agent. For each request you review a change to one file of \
a repository and leave a review comment on the pull request, using tools only.
Tools:
- read_file(path, start_line, end_line): read lines of a repository file.
- run_tests(selector): run the test suite (or the tests matching selector).
- format_diff(path, old, new): produce a unified diff of the file with `old` replaced by `new`.
- post_comment(target, body, path, line): post a review comment on a pull request.
- finish(summary): call once when the review comment has been posted.
Work step by step and call one tool at a time."""

FILES = ["src/app.py", "src/util.py", "src/models.py", "src/api.py", "src/config.py",
         "src/db.py", "src/auth.py", "src/cache.py", "src/cli.py", "src/jobs.py"]
ASKS = [
    "PR #{n} renames a variable in {path}. Review it: read the file, run the tests, "
    "show the diff and post your review.",
    "Please review PR #{n}, which touches {path}. Check the tests still pass, prepare the diff "
    "and leave a comment.",
    "Review the change to {path} in PR #{n}: look at the code, run the tests, format the diff, "
    "then comment on the PR.",
]


class Coding(Task):
    def _pr(self, i: int) -> tuple[int, str]:
        return 100 + i, FILES[i % len(FILES)]

    def user_prompt(self, i: int) -> str:
        n, path = self._pr(i)
        return ASKS[i % len(ASKS)].format(n=n, path=path)

    def scripted(self, i: int) -> list[tuple[str, dict[str, Any]]]:
        n, path = self._pr(i)
        return [("read_file", {"path": path, "start_line": 1, "end_line": 40}),
                ("run_tests", {"selector": ""}),
                ("format_diff", {"path": path, "old": "x", "new": "y"}),
                ("post_comment", {"target": f"pr/{n}", "body": "Looks good; tests pass.",
                                  "path": path, "line": 1})]


TASK = Coding(
    scenario="B", name="coding_review", system=SYSTEM,
    tools=[
        fn("read_file", "Read lines of a repository file.",
           {"path": {"type": "string"}, "start_line": {"type": "integer"},
            "end_line": {"type": "integer"}}, ["path"]),
        fn("run_tests", "Run tests (all, or those matching selector).",
           {"selector": {"type": "string"}}),
        fn("format_diff", "Unified diff of a file with `old` replaced by `new`.",
           {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
           ["path"]),
        fn("post_comment", "Post a review comment on a pull request.",
           {"target": {"type": "string", "description": "e.g. pr/101"},
            "body": {"type": "string"}, "path": {"type": "string"},
            "line": {"type": "integer"}}, ["target", "body"]),
        FINISH_TOOL],
    start="read_file",
    # PRD graph (deterministic): a fixed linear pipeline
    edges={"read_file": {"run_tests"}, "run_tests": {"format_diff"},
           "format_diff": {"post_comment"}, "post_comment": {"<end>"}},
    canonical=["read_file", "run_tests", "format_diff", "post_comment"])
