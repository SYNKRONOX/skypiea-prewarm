"""generic_fallback (heavy): serves every class without its own server (other_*, BFCL...).

Routes the raw tool name to a coarse intent with a scikit-learn model and returns a small
deterministic payload for it.
"""

import hashlib

import joblib
import numpy as np

from tools.common.model import model_path
from tools.common.server import burn, create_server, instrument, run

NAME = "generic_fallback"
MODEL = joblib.load(model_path(NAME))
PIPE = MODEL["pipeline"]
PIPE.predict(["warmup"])

mcp = create_server(NAME, "Generic fallback tool for any unmapped tool class.")


@mcp.tool(name=NAME)
@instrument(NAME)
async def generic_fallback(tool_name: str = "unknown", tool_class: str = "other",
                           arguments: dict | None = None,
                           service_ms: int | None = None) -> dict:
    """Execute (simulated) any tool: returns the routed intent and a deterministic result."""
    await burn(service_ms, "cpu")
    proba = PIPE.predict_proba([tool_name.lower()])[0]
    i = int(np.argmax(proba))
    digest = hashlib.sha256(f"{tool_name}|{sorted((arguments or {}).items())}".encode())
    return {"tool_name": tool_name, "tool_class": tool_class,
            "intent": str(PIPE.classes_[i]), "confidence": round(float(proba[i]), 4),
            "result": {"id": digest.hexdigest()[:12], "value": int(digest.hexdigest()[:6], 16)}}


if __name__ == "__main__":
    run(mcp, NAME, {"model_examples": MODEL["n_train"]})
