"""Generate the Skypiea Air mock database (fictional airline; no real names or brands).

    python -m agent.skyair.generate        # -> agent/skyair/skyair_db.json (deterministic)

Seed 2026: the same code always writes the same file (tests check it). About 50 customers
(some share a name on purpose, to exercise "ambiguous name -> ask again"), 150 bookings and a
two-week flight schedule between six fictional-code airports; a few flights are cancelled by
the airline (those bookings are refund-eligible whatever the fare).
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).with_name("skyair_db.json")
SEED = 2026
FIRST = ["Asha", "Ravi", "Meera", "Kiran", "Nisha", "Arjun", "Leela", "Vikram", "Tara", "Dev",
         "Anya", "Rohan", "Isha", "Kabir", "Maya", "Neel", "Zara", "Omar", "Lina", "Sam"]
LAST = ["Rao", "Iyer", "Menon", "Shah", "Kapoor", "Das", "Nair", "Bose", "Patel", "Singh",
        "Verma", "Joshi", "Reddy", "Kulkarni", "Fernandes"]
# fictional three-letter codes (not real airports' IATA codes on purpose)
AIRPORTS = {"SKA": "Skyport Alpha", "SKB": "Skyport Bay", "SKC": "Skyport Coast",
            "SKD": "Skyport Delta", "SKE": "Skyport East", "SKF": "Skyport Falls"}
FARES = {"Saver": {"change_fee": 50, "refundable": False},
         "Flex": {"change_fee": 0, "refundable": True},
         "Business": {"change_fee": 0, "refundable": True}}
START = date(2026, 12, 1)
DAYS = 14


def build(seed: int = SEED) -> dict:
    rnd = random.Random(seed)
    codes = sorted(AIRPORTS)
    routes = [(a, b) for a in codes for b in codes if a != b]
    flights, n = [], 100
    for d in range(DAYS):
        day = (START + timedelta(days=d)).isoformat()
        for a, b in rnd.sample(routes, 12):
            for dep in sorted(rnd.sample(["06:15", "09:40", "13:05", "17:30", "21:10"], 2)):
                n += 1
                dur = rnd.choice([55, 70, 85, 100, 125])
                h, m = map(int, dep.split(":"))
                t = h * 60 + m + dur
                arr = f"{t // 60 % 24:02d}:{t % 60:02d}"
                flights.append({"flight_no": f"SA{n}", "origin": a, "destination": b,
                                "date": day, "depart": dep, "arrive": arr,
                                "seats_left": rnd.randint(0, 40),
                                "price": rnd.choice([79, 99, 119, 149, 189, 229]),
                                "status": "cancelled_by_airline" if rnd.random() < 0.03
                                else "scheduled"})
    names = [f"{f} {lname}" for f in FIRST for lname in LAST]
    rnd.shuffle(names)
    chosen = names[:46] + names[:4]      # four names appear twice: ambiguous on purpose
    customers = []
    for i, name in enumerate(chosen):
        local = name.lower().replace(" ", ".")
        customers.append({"customer_id": f"C{1001 + i}", "name": name,
                          "email": f"{local}{i if i >= 46 else ''}@example.com",
                          "tier": rnd.choice(["Blue", "Blue", "Silver", "Gold"])})
    bookings, used = [], set()
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(150):
        c = rnd.choice(customers)
        code = ""
        while not code or code in used:
            code = "".join(rnd.choice(alphabet) for _ in range(6))
        used.add(code)
        out = rnd.choice(flights)
        segs = [out["flight_no"]]
        if rnd.random() < 0.4:   # return trip a few days later on the reverse route
            back = [f for f in flights if f["origin"] == out["destination"]
                    and f["destination"] == out["origin"] and f["date"] > out["date"]]
            if back:
                segs.append(rnd.choice(back)["flight_no"])
        pax = [c["name"]] + [f"{rnd.choice(FIRST)} {c['name'].split()[1]}"
                             for _ in range(rnd.choice([0, 0, 1, 2]))]
        fare = rnd.choice(["Saver", "Saver", "Flex", "Business"])
        bookings.append({"booking_code": code, "customer_id": c["customer_id"],
                         "flights": segs, "passengers": pax, "fare": fare,
                         "bags": rnd.choice([0, 1, 1, 2]), "status": "confirmed",
                         "paid": sum(next(f["price"] for f in flights if f["flight_no"] == s)
                                     for s in segs) * len(pax)})
    return {"airline": "Skypiea Air (fictional)", "seed": seed, "airports": AIRPORTS,
            "fare_rules": FARES, "bag_price": 35, "customers": customers,
            "flights": flights, "bookings": bookings}


def main() -> int:
    OUT.write_text(json.dumps(build(), indent=1, sort_keys=True) + "\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
