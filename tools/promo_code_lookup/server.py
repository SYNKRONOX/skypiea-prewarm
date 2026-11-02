"""promo_code_lookup (light): validate a promo code against an order total."""

from tools.common.data import load_json
from tools.common.server import burn, create_server, instrument, run

NAME = "promo_code_lookup"
PROMOS = load_json("promos")
BY_CODE = {p["code"]: p for p in PROMOS}

mcp = create_server(NAME, "Look up and validate a promo code.")


@mcp.tool(name=NAME)
@instrument(NAME)
async def promo_code_lookup(code: str = "", order_total: float = 0.0, category: str = "any",
                            day: int = 180, service_ms: int | None = None) -> dict:
    """Return the promo's terms and whether it applies to this order."""
    await burn(service_ms, "cpu")
    promo = BY_CODE.get(code)
    if promo is None:
        return {"code": code, "found": False, "valid": False, "reason": "unknown code"}
    reasons = []
    if day > promo["expires_day"]:
        reasons.append("expired")
    if order_total < promo["min_total"]:
        reasons.append(f"minimum total is {promo['min_total']}")
    if promo["category"] not in ("any", category):
        reasons.append(f"only for {promo['category']}")
    discount = round(order_total * promo["percent_off"] / 100, 2) if not reasons else 0.0
    return {"code": code, "found": True, "valid": not reasons, "reasons": reasons,
            "percent_off": promo["percent_off"], "discount": discount}


if __name__ == "__main__":
    run(mcp, NAME, {"promos": len(PROMOS)})
