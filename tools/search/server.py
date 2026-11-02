"""search (medium): grep-like search over the sample repo, indexed with pandas at startup."""

import fnmatch
import re

import numpy as np
import pandas as pd

from tools.common.data import SAMPLE_REPO, repo_files
from tools.common.server import burn, create_server, instrument, run

NAME = "search"
_rows = []
for rel in repo_files():
    for i, text in enumerate((SAMPLE_REPO / rel).read_text(encoding="utf-8").splitlines(), 1):
        _rows.append((rel, i, text))
LINES = pd.DataFrame(_rows, columns=["path", "line", "text"])
LOWER = LINES["text"].str.lower().to_numpy()
FILE_SIZES = LINES.groupby("path").size()

mcp = create_server(NAME, "Search the sample repository.")


@mcp.tool(name=NAME)
@instrument(NAME)
async def search(query: str = "def", regex: bool = False, glob: str = "",
                 max_results: int = 20, service_ms: int | None = None) -> dict:
    """Find lines matching query (substring, or regex if regex=true); or list files by glob."""
    await burn(service_ms, "io")
    if glob:
        files = [p for p in FILE_SIZES.index if fnmatch.fnmatch(p, glob)]
        return {"glob": glob, "files": files}
    if regex:
        try:
            rx = re.compile(query, re.IGNORECASE)
        except re.error as e:
            return {"query": query, "error": str(e), "matches": []}
        mask = np.fromiter((bool(rx.search(t)) for t in LINES["text"]), bool, len(LINES))
    else:
        q = query.lower()
        mask = np.fromiter((q in t for t in LOWER), bool, len(LOWER))
    hits = LINES[mask].head(max_results)
    return {"query": query, "total": int(mask.sum()), "matches": hits.to_dict("records")}


if __name__ == "__main__":
    run(mcp, NAME, {"lines": len(LINES), "files": len(FILE_SIZES)})
