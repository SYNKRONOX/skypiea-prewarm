"""refund_issue (light): refund delivered order items (simulated)."""

from tools.common.data import load_json
from tools.common.server import burn, create_server, instrument, run

NAME = "refund_issue"
ORDERS = {o["order_id"]: o for o in load_json("orders")}
ORDER_IDS = sorted(ORDERS)

mcp = create_server(NAME, "Issue a refund for delivered order items.")


@mcp.tool(name=NAME)
@instrument(NAME)
async def refund_issue(order_id: str = "", product_ids: list[str] | None = None,
                       service_ms: int | None = None) -> dict:
    """Refund the given products (default: all) of a delivered order."""
    await burn(service_ms, "io")
    order = ORDERS.get(order_id) or ORDERS[ORDER_IDS[sum(map(ord, order_id)) % len(ORDER_IDS)]]
    items = [i for i in order["items"] if not product_ids or i["product_id"] in product_ids]
    amount = round(sum(i["qty"] * i["price"] for i in items), 2)
    ok = order["status"] == "delivered"
    method = "store_credit" if order["payment_method"] == "gift_card" else order["payment_method"]
    return {"order_id": order["order_id"], "accepted": ok, "amount": amount if ok else 0.0,
            "refund_to": method, "items": [i["product_id"] for i in items]}


if __name__ == "__main__":
    run(mcp, NAME, {"orders": len(ORDERS)})
