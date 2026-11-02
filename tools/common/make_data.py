"""Generate the small, deterministic fake support data bundled into the tool images.

    python tools/common/make_data.py      # rewrites tools/common/data/support/*.json

Seeded (0), so re-running produces byte-identical files.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent / "data" / "support"

FIRST = ["Asha", "Ravi", "Meera", "Arjun", "Kavya", "Vikram", "Diya", "Rohan", "Isha", "Karan",
         "Neha", "Aditya", "Priya", "Sanjay", "Lakshmi", "Rahul", "Ananya", "Dev", "Tara", "Nikhil"]
LAST = ["Rao", "Iyer", "Shah", "Reddy", "Nair", "Gupta", "Menon", "Das", "Pillai", "Kumar"]
CATEGORIES = ["electronics", "home", "books", "clothing", "sports", "toys", "beauty", "garden"]
ADJ = ["compact", "wireless", "classic", "eco", "smart", "deluxe", "portable", "ergonomic"]
NOUN = ["lamp", "speaker", "kettle", "backpack", "headphones", "mug", "jacket", "notebook",
        "yoga mat", "drone", "blender", "watch", "router", "chair", "puzzle", "racket"]
AIRPORTS = ["BLR", "DEL", "BOM", "MAA", "HYD", "CCU", "GOI", "COK", "PNQ", "AMD"]
POLICY_TOPICS = {
    "refund": [
        "Delivered items can be returned within 30 days for a full refund to the original "
        "payment method.",
        "Refunds for gift cards are not allowed; items paid by gift card are refunded as "
        "store credit.",
        "Opened electronics are refundable within 15 days minus a 10 percent restocking fee.",
        "Refunds are issued within 5 business days after the returned item is received.",
    ],
    "exchange": [
        "Delivered items can be exchanged once for an item of the same product type.",
        "Exchanges with a price difference charge or refund the difference to the payment "
        "method.",
        "Clothing can be exchanged for a different size within 45 days.",
    ],
    "cancellation": [
        "Pending orders can be cancelled in full; partially shipped orders cannot be "
        "cancelled.",
        "Cancellation reasons must be either no longer needed or ordered by mistake.",
        "Flights can be cancelled within 24 hours of booking for a full refund.",
    ],
    "modification": [
        "Pending orders can have items, address or payment modified once.",
        "Reservations in basic economy cannot be modified.",
        "Baggage can be added to a reservation but never removed.",
    ],
    "promo": [
        "Only one promo code may be applied per order.",
        "Promo codes cannot be applied to orders that are already delivered.",
        "Expired promo codes are never honoured, even on request.",
    ],
    "escalation": [
        "Transfer to a human agent only when the request is outside policy and the user "
        "insists.",
        "Never disclose another user's personal data; verify identity before any change.",
    ],
}


def main() -> None:
    rng = random.Random(0)
    OUT.mkdir(parents=True, exist_ok=True)

    users = []
    for i in range(200):
        f, last = rng.choice(FIRST), rng.choice(LAST)
        users.append({
            "user_id": f"{f.lower()}_{last.lower()}_{1000 + i}",
            "name": f"{f} {last}",
            "email": f"{f.lower()}.{last.lower()}{i}@example.com",
            "zip": f"{560000 + rng.randrange(100):06d}",
            "tier": rng.choice(["regular", "silver", "gold"]),
            "payment_methods": rng.sample(["credit_card", "paypal", "gift_card"], k=2),
        })

    products = []
    for i in range(150):
        products.append({
            "product_id": f"P{10000 + i}",
            "name": f"{rng.choice(ADJ)} {rng.choice(NOUN)}",
            "category": rng.choice(CATEGORIES),
            "price": round(rng.uniform(5, 500), 2),
            "stock": rng.randrange(0, 200),
            "rating": round(rng.uniform(2.5, 5.0), 1),
        })

    orders = []
    for i in range(2000):
        user = rng.choice(users)
        items = []
        for p in rng.sample(products, k=rng.randint(1, 4)):
            items.append({"product_id": p["product_id"], "qty": rng.randint(1, 3),
                          "price": p["price"]})
        orders.append({
            "order_id": f"#W{100000 + i}",
            "user_id": user["user_id"],
            "status": rng.choice(["pending", "processed", "shipped", "delivered", "delivered",
                                  "cancelled"]),
            "items": items,
            "total": round(sum(it["qty"] * it["price"] for it in items), 2),
            "day": rng.randrange(1, 366),
            "payment_method": rng.choice(user["payment_methods"]),
        })

    policies = []
    for topic, texts in POLICY_TOPICS.items():
        for j, text in enumerate(texts):
            policies.append({"policy_id": f"{topic.upper()}-{j + 1}", "topic": topic,
                             "text": text})

    promos = []
    for _ in range(40):
        promos.append({
            "code": f"SKY{rng.randrange(10**5):05d}",
            "percent_off": rng.choice([5, 10, 15, 20, 25]),
            "min_total": rng.choice([0, 50, 100, 200]),
            "category": rng.choice(CATEGORIES + ["any", "any"]),
            "expires_day": rng.randrange(1, 400),
        })

    flights = []
    for i in range(600):
        o, d = rng.sample(AIRPORTS, k=2)
        flights.append({
            "flight_no": f"SK{100 + i}",
            "origin": o,
            "destination": d,
            "day": rng.randrange(1, 61),
            "depart": f"{rng.randrange(5, 23):02d}:{rng.choice(['00', '15', '30', '45'])}",
            "price": rng.randrange(2500, 15000),
            "seats_left": rng.randrange(0, 40),
            "cabin": rng.choice(["basic_economy", "economy", "business"]),
        })

    reservations = []
    for _ in range(300):
        fl = rng.choice(flights)
        reservations.append({
            "reservation_id": f"R{rng.randrange(36**5):07d}",
            "user_id": rng.choice(users)["user_id"],
            "flight_no": fl["flight_no"],
            "cabin": fl["cabin"],
            "passengers": rng.randint(1, 4),
            "bags": rng.randint(0, 3),
        })

    for name, rows in [("users", users), ("products", products), ("orders", orders),
                       ("policies", policies), ("promos", promos), ("flights", flights),
                       ("reservations", reservations)]:
        (OUT / f"{name}.json").write_text(json.dumps(rows, indent=0, sort_keys=True) + "\n",
                                          encoding="utf-8")
        print(f"{name}: {len(rows)} rows")


if __name__ == "__main__":
    main()
