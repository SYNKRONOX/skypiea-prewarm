"""read_file (light): read a file from the bundled sample repo."""

from tools.common.data import repo_files, repo_path
from tools.common.server import burn, create_server, instrument, run

NAME = "read_file"
FILES = repo_files()

mcp = create_server(NAME, "Read a file from the sample repository.")


@mcp.tool(name=NAME)
@instrument(NAME)
async def read_file(path: str = "", start_line: int = 1, end_line: int = 0,
                    service_ms: int | None = None) -> dict:
    """Return lines [start_line, end_line] of a repo file (unknown paths map to a real file)."""
    await burn(service_ms, "io")
    if path not in FILES:
        path = FILES[sum(map(ord, path)) % len(FILES)]
    lines = repo_path(path).read_text(encoding="utf-8").splitlines()
    end = len(lines) if end_line <= 0 else min(end_line, len(lines))
    start = max(1, start_line)
    return {"path": path, "start_line": start, "end_line": end, "total_lines": len(lines),
            "content": "\n".join(lines[start - 1:end])}


if __name__ == "__main__":
    run(mcp, NAME, {"files": len(FILES)})
