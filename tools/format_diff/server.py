"""format_diff (light): turn a proposed edit into a unified diff (nothing is written)."""

import difflib

from tools.common.data import repo_files, repo_path
from tools.common.server import burn, create_server, instrument, run

NAME = "format_diff"
FILES = repo_files()

mcp = create_server(NAME, "Format a proposed file edit as a unified diff.")


@mcp.tool(name=NAME)
@instrument(NAME)
async def format_diff(path: str = "", old: str = "", new: str = "", new_content: str = "",
                      service_ms: int | None = None) -> dict:
    """Diff a repo file against new_content, or against the file with `old` replaced by `new`."""
    await burn(service_ms, "io")
    if path not in FILES:
        path = FILES[sum(map(ord, path)) % len(FILES)]
    before = repo_path(path).read_text(encoding="utf-8")
    if new_content:
        after = new_content
    elif old and old in before:
        after = before.replace(old, new, 1)
    else:
        after = before + (new or "# reviewed by skypiea\n")
    diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                        fromfile=f"a/{path}", tofile=f"b/{path}"))
    added = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff.splitlines()
                  if ln.startswith("-") and not ln.startswith("---"))
    return {"path": path, "added": added, "removed": removed, "diff": diff}


if __name__ == "__main__":
    run(mcp, NAME, {"files": len(FILES)})
