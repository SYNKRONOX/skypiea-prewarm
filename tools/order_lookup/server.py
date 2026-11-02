"""order_lookup (medium): order details from a pandas table loaded at startup."""

import numpy as np
import pandas as pd

from tools.common.data import SUPPORT_DIR
from tools.common.server import burn, create_server, instrument, run

NAME = "order_lookup"
ORDERS = pd.read_json(SUPPORT_DIR / "orders.json").set_index("order_id", drop=False)
USERS = pd.read_json(SUPPORT_DIR / "users.json").set_index("user_id", drop=False)
ORDERS["n_items"] = ORDERS["items"].map(len).astype(np.int64)
SPEND = ORDERS.groupby("user_id")["total"].agg(["sum", "count"])  # precomputed at startup

mcp = create_server(NAME, "Look up an order or a user's orders.")


@mcp.tool(name=NAME)
@instrument(NAME)
async def order_lookup(order_id: str = "", user_id: str = "",
                       service_ms: int | None = None) -> dict:
    """Return one order by id, or a summary of a user's orders."""
    await burn(service_ms, "cpu")
    if order_id or not user_id:
        if order_id not in ORDERS.index:
            order_id = ORDERS.index[sum(map(ord, order_id)) % len(ORDERS)]
        row = ORDERS.loc[order_id]
        return {"order_id": order_id, "user_id": row["user_id"], "status": row["status"],
                "items": row["items"], "total": float(row["total"]),
                "payment_method": row["payment_method"]}
    if user_id not in SPEND.index:
        user_id = SPEND.index[sum(map(ord, user_id)) % len(SPEND)]
    mine = ORDERS[ORDERS["user_id"] == user_id]
    return {"user_id": user_id, "orders": mine["order_id"].tolist()[:20],
            "count": int(SPEND.loc[user_id, "count"]),
            "lifetime_total": round(float(SPEND.loc[user_id, "sum"]), 2)}


if __name__ == "__main__":
    run(mcp, NAME, {"orders": len(ORDERS), "users": len(USERS)})
