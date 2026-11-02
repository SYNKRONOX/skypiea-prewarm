"""edit_file (light): apply an edit to a file of the sample repo, in a per-call scratch copy.

The bundled repo is never modified: the edit is written to a temporary file (real file I/O),
read back, and summarised, so repeated calls return the same deterministic result.
"""

import hashlib
import os
import tempfile

from tools.common.data import repo_files, repo_path
from tools.common.server import burn, create_server, instrument, run

NAME = "edit_file"
FILES = repo_files()

mcp = create_server(NAME, "Edit or create a file in the sample repository.")


@mcp.tool(name=NAME)
@instrument(NAME)
async def edit_file(path: str = "", old: str = "", new: str = "", content: str = "",
                    service_ms: int | None = None) -> dict:
    """Replace `old` with `new` in a repo file, or write `content` as the whole file."""
    await burn(service_ms, "io")
    created = path not in FILES
    before = "" if created else repo_path(path).read_text(encoding="utf-8")
    if content:
        after = content
    elif old and old in before:
        after = before.replace(old, new, 1)
    else:
        after = before + (new or "# edited by skypiea\n")
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".edit",
                                     dir=os.environ.get("SKYPIEA_SCRATCH")) as f:
        f.write(after)
        f.flush()
        f.seek(0)
        written = f.read()
    data = written.encode()
    return {"path": path or "untitled.txt", "created": created, "bytes": len(data),
            "lines": written.count("\n"), "sha256": hashlib.sha256(data).hexdigest()[:16],
            "replaced": bool(old and old in before)}


if __name__ == "__main__":
    run(mcp, NAME, {"files": len(FILES)})
