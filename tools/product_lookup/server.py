"""product_lookup (medium): product details and category stats from pandas/numpy."""

import numpy as np
import pandas as pd

from tools.common.data import SUPPORT_DIR
from tools.common.server import burn, create_server, instrument, run

NAME = "product_lookup"
PRODUCTS = pd.read_json(SUPPORT_DIR / "products.json").set_index("product_id", drop=False)
STATS = PRODUCTS.groupby("category")["price"].agg(["mean", "min", "max", "count"])
PRICES = PRODUCTS["price"].to_numpy(dtype=np.float64)

mcp = create_server(NAME, "Look up a product or list a category.")


@mcp.tool(name=NAME)
@instrument(NAME)
async def product_lookup(product_id: str = "", category: str = "",
                         service_ms: int | None = None) -> dict:
    """Return a product by id, or the cheapest in-stock products of a category."""
    await burn(service_ms, "cpu")
    if category and category in STATS.index:
        sub = PRODUCTS[(PRODUCTS["category"] == category) & (PRODUCTS["stock"] > 0)]
        sub = sub.sort_values(["price", "product_id"]).head(5)
        s = STATS.loc[category]
        return {"category": category, "count": int(s["count"]),
                "mean_price": round(float(s["mean"]), 2),
                "products": sub[["product_id", "name", "price"]].to_dict("records")}
    if product_id not in PRODUCTS.index:
        product_id = PRODUCTS.index[sum(map(ord, product_id)) % len(PRODUCTS)]
    row = PRODUCTS.loc[product_id]
    pct = float((PRICES < row["price"]).mean() * 100)
    return {"product_id": product_id, "name": row["name"], "category": row["category"],
            "price": float(row["price"]), "stock": int(row["stock"]),
            "rating": float(row["rating"]), "price_percentile": round(pct, 1)}


if __name__ == "__main__":
    run(mcp, NAME, {"products": len(PRODUCTS)})
