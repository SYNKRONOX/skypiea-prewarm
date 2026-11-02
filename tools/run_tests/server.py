"""run_tests (heavy): collect the sample repo's tests, rank them by a learned failure risk."""

import ast
import hashlib

import joblib
import numpy as np

from tools.common.data import SAMPLE_REPO, repo_files
from tools.common.model import model_path
from tools.common.server import burn, create_server, instrument, run

NAME = "run_tests"
MODEL = joblib.load(model_path(NAME))
CLF = MODEL["model"]


def _collect() -> list[str]:
    tests = []
    for rel in repo_files():
        if rel.endswith(".py") and "/test_" in f"/{rel}":
            tree = ast.parse((SAMPLE_REPO / rel).read_text(encoding="utf-8"))
            tests += [f"{rel}::{n.name}" for n in tree.body
                      if isinstance(n, ast.FunctionDef) and n.name.startswith("test")]
    return tests


TESTS = _collect()


def _features(test_id: str, lines_changed: int, files_touched: int) -> list[float]:
    h = hashlib.sha256(test_id.encode()).digest()
    return [lines_changed, files_touched, h[0] * 3, h[1] % 3, 20 + h[2]]


CLF.predict_proba(np.array([_features("warmup", 1, 1)], dtype=np.float32))

mcp = create_server(NAME, "Run the sample repo's tests.")


@mcp.tool(name=NAME)
@instrument(NAME)
async def run_tests(selector: str = "", lines_changed: int = 40, files_touched: int = 2,
                    service_ms: int | None = None) -> dict:
    """Run tests matching selector (default: all). Returns results and failure-risk order."""
    await burn(service_ms, "cpu")
    chosen = [t for t in TESTS if selector in t] or TESTS
    X = np.array([_features(t, lines_changed, files_touched) for t in chosen], dtype=np.float32)
    risk = CLF.predict_proba(X)[:, 1]
    order = np.argsort(-risk, kind="stable")
    return {"selector": selector, "collected": len(chosen), "passed": len(chosen), "failed": 0,
            "by_risk": [{"test": chosen[i], "risk": round(float(risk[i]), 4)} for i in order]}


if __name__ == "__main__":
    run(mcp, NAME, {"tests": len(TESTS), "trees": len(CLF.estimators_)})
