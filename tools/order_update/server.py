"""order_update (light): modify, cancel or exchange an order (simulated, no state kept)."""

from tools.common.data import load_json
from tools.common.server import burn, create_server, instrument, run

NAME = "order_update"
ORDERS = {o["order_id"]: o for o in load_json("orders")}
ORDER_IDS = sorted(ORDERS)
ALLOWED = {"modify": {"pending"}, "cancel": {"pending"}, "exchange": {"delivered"}}

mcp = create_server(NAME, "Modify, cancel or exchange an order.")


@mcp.tool(name=NAME)
@instrument(NAME)
async def order_update(order_id: str = "", action: str = "modify", reason: str = "",
                       service_ms: int | None = None) -> dict:
    """Apply action (modify | cancel | exchange) to an order, following store policy."""
    await burn(service_ms, "io")
    order = ORDERS.get(order_id) or ORDERS[ORDER_IDS[sum(map(ord, order_id)) % len(ORDER_IDS)]]
    ok = order["status"] in ALLOWED.get(action, set())
    new_status = {"modify": "pending", "cancel": "cancelled",
                  "exchange": "exchange requested"}.get(action, order["status"])
    return {"order_id": order["order_id"], "action": action, "accepted": ok,
            "status": new_status if ok else order["status"],
            "message": "done" if ok else f"cannot {action} an order that is {order['status']}"}


if __name__ == "__main__":
    run(mcp, NAME, {"orders": len(ORDERS)})
