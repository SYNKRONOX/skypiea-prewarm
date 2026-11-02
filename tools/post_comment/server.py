"""post_comment (light): post a review comment / user message (simulated; nothing is stored)."""

import hashlib

from tools.common.server import burn, create_server, instrument, run

NAME = "post_comment"
mcp = create_server(NAME, "Post a comment on a pull request or message the user.")


@mcp.tool(name=NAME)
@instrument(NAME)
async def post_comment(target: str = "pr/1", body: str = "", path: str = "", line: int = 0,
                       service_ms: int | None = None) -> dict:
    """Post `body` on `target` (optionally on path:line). Returns a deterministic comment id."""
    await burn(service_ms, "io")
    cid = hashlib.sha256(f"{target}|{path}|{line}|{body}".encode()).hexdigest()[:16]
    return {"comment_id": cid, "target": target, "path": path, "line": line,
            "length": len(body)}


if __name__ == "__main__":
    run(mcp, NAME)
