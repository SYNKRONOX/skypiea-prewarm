"""policy_check (heavy): classify a request to a policy topic with a scikit-learn model."""

import joblib
import numpy as np

from tools.common.data import load_json
from tools.common.model import model_path
from tools.common.server import burn, create_server, instrument, run

NAME = "policy_check"
MODEL = joblib.load(model_path(NAME))
PIPE = MODEL["pipeline"]
POLICIES = load_json("policies")
PIPE.predict(["warm up the model"])  # first predict compiles lazy paths; part of startup

mcp = create_server(NAME, "Check which store policy applies to a request.")


@mcp.tool(name=NAME)
@instrument(NAME)
async def policy_check(request: str = "refund my order", order_status: str = "",
                       service_ms: int | None = None) -> dict:
    """Return the policy topic, its confidence, the matching policy texts and a verdict."""
    await burn(service_ms, "cpu")
    proba = PIPE.predict_proba([request.lower()])[0]
    classes = PIPE.classes_
    i = int(np.argmax(proba))
    topic = str(classes[i])
    allowed = {
        "refund": order_status in ("", "delivered"),
        "exchange": order_status in ("", "delivered"),
        "cancellation": order_status in ("", "pending"),
        "modification": order_status in ("", "pending"),
    }.get(topic, True)
    return {"topic": topic, "confidence": round(float(proba[i]), 4), "allowed": allowed,
            "policies": [p for p in POLICIES if p["topic"] == topic]}


if __name__ == "__main__":
    run(mcp, NAME, {"model_examples": MODEL["n_train"]})
