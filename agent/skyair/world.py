"""Skypiea Air demo world: SYNTHETIC, India-centred, generated in memory from a fixed seed.

    python -m agent.skyair.world            # prints a summary (nothing is written)

Everything here is fictional except the airports: real IATA codes, city names and approximate
coordinates of 38 Indian and 8 international airports (public facts, used for distances). The
airline, flight numbers, timetable, fares, customers, bookings and offers are invented by the
rules below; no real airline data is used or scraped. Only this generator and its seed are in
the repository: the world is rebuilt identically on every start (tests check it).

Rules (docs/SKYPIEA_WORLD.md has the plain-language version):
- Demo-world "today" is ANCHOR (fixed, shown in the console; never the real date). Timetable:
  PAST_DAYS before today (for flown bookings) and DAYS from today, all times IST.
- Hub-and-spoke: DEL, BOM, BLR, HYD, MAA, CCU are hubs, connected to each other; every other
  Indian airport is served from its 1-3 nearest hubs (more for bigger cities; cities of size
  3 also from DEL and BOM); international
  airports from the hubs and a few large cities. Daily frequency grows with both cities' size.
- Block time = 30 min taxi + great-circle distance at 780 km/h, rounded to 5 min.
- Saver fare (INR) = (1,400 + 4.1 x km domestic; 5,500 + 5.2 x km international)
  x weekday factor (Fri, Sun 1.15; Sat 1.05; else 1.00)
  x advance-purchase factor from today (>= 30 days 0.85; 14-29 1.00; 7-13 1.15; < 7 1.35)
  x a fixed per-departure factor 0.95-1.10 (from the seed), rounded to 10.
  Flex = Saver x 1.35; Business = Saver x 3.0 (rounded to 10).
- Change, cancel, refund and baggage rules: FARE_RULES and POLICY below.
"""

from __future__ import annotations

import math
import random
import zlib
from datetime import date, timedelta
from functools import lru_cache
from typing import Any

SEED = 20270111
ANCHOR = date(2027, 1, 11)          # demo-world "today" (a Monday)
DAYS = 90                           # timetable from today on
PAST_DAYS = 21                      # flown bookings
AIRLINE = "Skypiea Air (fictional)"
CURRENCY = "INR"

# code: (city, airport name, lat, lon, size 1..5, aliases)
AIRPORTS: dict[str, tuple[str, str, float, float, int, tuple[str, ...]]] = {
    "DEL": ("Delhi", "Indira Gandhi International", 28.556, 77.100, 5, ("new delhi",)),
    "BOM": ("Mumbai", "Chhatrapati Shivaji Maharaj International", 19.089, 72.868, 5,
            ("bombay",)),
    "BLR": ("Bengaluru", "Kempegowda International", 13.199, 77.706, 5, ("bangalore",)),
    "HYD": ("Hyderabad", "Rajiv Gandhi International", 17.240, 78.429, 4, ()),
    "MAA": ("Chennai", "Chennai International", 12.994, 80.171, 4, ("madras",)),
    "CCU": ("Kolkata", "Netaji Subhas Chandra Bose International", 22.655, 88.447, 4,
            ("calcutta",)),
    "AMD": ("Ahmedabad", "Sardar Vallabhbhai Patel International", 23.077, 72.635, 3, ()),
    "PNQ": ("Pune", "Pune Airport", 18.582, 73.920, 3, ("poona",)),
    "GOI": ("Goa", "Goa International (Dabolim)", 15.381, 73.831, 3, ("dabolim",)),
    "COK": ("Kochi", "Cochin International", 10.152, 76.402, 3, ("cochin",)),
    "TRV": ("Thiruvananthapuram", "Trivandrum International", 8.482, 76.920, 2,
            ("trivandrum",)),
    "JAI": ("Jaipur", "Jaipur International", 26.824, 75.812, 3, ()),
    "LKO": ("Lucknow", "Chaudhary Charan Singh International", 26.761, 80.889, 3, ()),
    "GAU": ("Guwahati", "Lokpriya Gopinath Bordoloi International", 26.106, 91.586, 3, ()),
    "PAT": ("Patna", "Jay Prakash Narayan Airport", 25.591, 85.088, 2, ()),
    "IXC": ("Chandigarh", "Chandigarh Airport", 30.673, 76.788, 2, ()),
    "VNS": ("Varanasi", "Lal Bahadur Shastri Airport", 25.452, 82.859, 2, ("banaras",)),
    "IDR": ("Indore", "Devi Ahilya Bai Holkar Airport", 22.722, 75.801, 2, ()),
    "NAG": ("Nagpur", "Dr. Babasaheb Ambedkar International", 21.092, 79.047, 2, ()),
    "BBI": ("Bhubaneswar", "Biju Patnaik International", 20.244, 85.818, 2, ()),
    "SXR": ("Srinagar", "Srinagar Airport", 33.987, 74.774, 2, ()),
    "IXB": ("Bagdogra", "Bagdogra Airport", 26.681, 88.329, 1, ("siliguri",)),
    "CJB": ("Coimbatore", "Coimbatore International", 11.030, 77.043, 2, ()),
    "VTZ": ("Visakhapatnam", "Visakhapatnam Airport", 17.721, 83.225, 2, ("vizag",)),
    "RPR": ("Raipur", "Swami Vivekananda Airport", 21.180, 81.739, 1, ()),
    "BHO": ("Bhopal", "Raja Bhoj Airport", 23.288, 77.337, 1, ()),
    "IXR": ("Ranchi", "Birsa Munda Airport", 23.314, 85.322, 1, ()),
    "ATQ": ("Amritsar", "Sri Guru Ram Dass Jee International", 31.708, 74.797, 1, ()),
    "IXZ": ("Port Blair", "Veer Savarkar International", 11.641, 92.730, 1,
            ("sri vijaya puram", "andaman")),
    "IMF": ("Imphal", "Imphal International", 24.760, 93.897, 1, ()),
    "DED": ("Dehradun", "Jolly Grant Airport", 30.190, 78.180, 1, ()),
    "UDR": ("Udaipur", "Maharana Pratap Airport", 24.618, 73.896, 1, ()),
    "IXE": ("Mangaluru", "Mangaluru International", 12.961, 74.890, 1, ("mangalore",)),
    "IXM": ("Madurai", "Madurai Airport", 9.834, 78.093, 1, ()),
    "TRZ": ("Tiruchirappalli", "Tiruchirappalli International", 10.765, 78.710, 1,
            ("trichy",)),
    "STV": ("Surat", "Surat Airport", 21.114, 72.742, 1, ()),
    "IXJ": ("Jammu", "Jammu Airport", 32.689, 74.837, 1, ()),
    "VGA": ("Vijayawada", "Vijayawada Airport", 16.530, 80.797, 1, ()),
    # international
    "DXB": ("Dubai", "Dubai International", 25.253, 55.364, 5, ()),
    "DOH": ("Doha", "Hamad International", 25.273, 51.608, 4, ()),
    "AUH": ("Abu Dhabi", "Zayed International", 24.433, 54.651, 3, ()),
    "SIN": ("Singapore", "Singapore Changi", 1.364, 103.991, 5, ()),
    "BKK": ("Bangkok", "Suvarnabhumi", 13.690, 100.750, 4, ()),
    "KUL": ("Kuala Lumpur", "Kuala Lumpur International", 2.745, 101.710, 3, ("kl",)),
    "CMB": ("Colombo", "Bandaranaike International", 7.181, 79.884, 3, ()),
    "LHR": ("London", "Heathrow", 51.470, -0.454, 5, ("heathrow",)),
}
INTERNATIONAL = {"DXB", "DOH", "AUH", "SIN", "BKK", "KUL", "CMB", "LHR"}
HUBS = ["DEL", "BOM", "BLR", "HYD", "MAA", "CCU"]
# international routes: from these Indian airports (fictional network)
INTL_FROM = {"DXB": ["DEL", "BOM", "BLR", "HYD", "MAA", "COK", "TRV", "AMD", "LKO", "CCU"],
             "DOH": ["DEL", "BOM", "HYD", "COK", "MAA"],
             "AUH": ["DEL", "BOM", "COK", "TRV"],
             "SIN": ["DEL", "BOM", "BLR", "MAA", "CCU", "HYD"],
             "BKK": ["DEL", "BOM", "CCU", "BLR"],
             "KUL": ["DEL", "BLR", "MAA"],
             "CMB": ["MAA", "BLR", "BOM", "DEL"],
             "LHR": ["DEL", "BOM"]}
FARES = ("Saver", "Flex", "Business")
FARE_FACTOR = {"Saver": 1.0, "Flex": 1.35, "Business": 3.0}
# per fare: change fee per passenger (domestic, international), refund rule, cabin bags kg
FARE_RULES = {
    "Saver": {"change_fee": 2500, "change_fee_intl": 5000, "refundable": False,
              "cancel_fee": None, "checked_kg": 15, "pieces": 1},
    "Flex": {"change_fee": 0, "change_fee_intl": 0, "refundable": True, "cancel_fee": 750,
             "checked_kg": 25, "pieces": 1},
    "Business": {"change_fee": 0, "change_fee_intl": 0, "refundable": True, "cancel_fee": 0,
                 "checked_kg": 35, "pieces": 2},
}
BAG_PRICE = {"domestic": 1800, "international": 3500}     # one extra 15 kg bag
POLICY = (
    "Skypiea Air policy (fictional). Changes: Saver pays a change fee of INR 2,500 per "
    "passenger (INR 5,000 on international flights) plus any fare difference; Flex and "
    "Business change for free (fare difference only). A change keeps the route. "
    "Cancellation: Saver is not refundable; Flex is refunded minus a INR 750 cancellation fee "
    "per passenger; Business is refunded in full; if Skypiea Air cancelled a flight, any fare "
    "is refunded in full. Refunds go to the original payment method only. Baggage: Saver "
    "includes 15 kg checked, Flex 25 kg, Business 2 x 35 kg; an extra 15 kg bag costs "
    "INR 1,800 (INR 3,500 international), 1-3 extra bags per booking. One promo per booking. "
    "There is no First class and no seat upgrade for points.")
OFFERS = [
    {"promo_code": "SKYWINTER10", "title": "Winter sale: 10% off your fare", "kind": "pct",
     "value": 10, "conditions": "All flights between 11 Jan and 28 Feb 2027; fare paid at "
     "least INR 3,000; one promo per booking."},
    {"promo_code": "GOAWEEKEND", "title": "15% off Saver fares to Goa on Fri and Sat",
     "kind": "pct", "value": 15, "conditions": "Saver fare; all flights to Goa (GOI) depart "
     "on a Friday or Saturday; one promo per booking."},
    {"promo_code": "BAG500", "title": "INR 500 back on an extra checked bag", "kind": "credit",
     "value": 500, "conditions": "Bookings with at least one extra checked bag; one promo "
     "per booking."},
    {"promo_code": "BIZ1500", "title": "INR 1,500 off Business", "kind": "credit",
     "value": 1500, "conditions": "Business fare bookings only; one promo per booking."},
]
FIRST_IN = ["Aarav", "Vivaan", "Aditya", "Arjun", "Sai", "Reyansh", "Krishna", "Ishaan",
            "Rohan", "Kabir", "Vikram", "Rahul", "Siddharth", "Anil", "Suresh", "Rajesh",
            "Manoj", "Imran", "Farhan", "Joseph", "Harpreet", "Gurpreet", "Ananya", "Diya",
            "Aadhya", "Saanvi", "Meera", "Priya", "Kavya", "Isha", "Neha", "Pooja", "Lakshmi",
            "Sunita", "Fatima", "Ayesha", "Zoya", "Mary", "Anjali", "Tara", "Asha", "Nisha",
            "Deepika", "Shreya", "Riya", "Simran", "Leela", "Gauri", "Dev", "Nikhil"]
LAST_IN = ["Sharma", "Verma", "Gupta", "Singh", "Kumar", "Patel", "Shah", "Mehta", "Iyer",
           "Iyengar", "Nair", "Menon", "Pillai", "Reddy", "Rao", "Naidu", "Das", "Bose",
           "Banerjee", "Chatterjee", "Mukherjee", "Sen", "Ghosh", "Kulkarni", "Deshpande",
           "Joshi", "Patil", "Jain", "Agarwal", "Khan", "Siddiqui", "Qureshi", "D'Souza",
           "Fernandes", "Gill", "Sandhu", "Bhat", "Hegde", "Shetty", "Kapoor", "Malhotra",
           "Chopra", "Thomas", "Varghese", "Mishra", "Pandey", "Tiwari", "Yadav"]
FIRST_INTL = ["Liam", "Olivia", "Noah", "Emma", "Mohammed", "Layla", "Wei", "Mei", "Hiroshi",
              "Yuki", "Lucas", "Sofia", "Ahmed", "Nour", "Daniel", "Grace", "Somchai",
              "Siti", "Nimal", "Chathuri"]
LAST_INTL = ["Smith", "Brown", "Wilson", "Al-Farsi", "Haddad", "Tan", "Lim", "Wong",
             "Tanaka", "Sato", "Silva", "Garcia", "Rahman", "Hassan", "Perera", "Fernando",
             "Ong", "Abdullah", "Taylor", "Martin"]
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
# Persona-card customers (fixed, so the console's persona cards, scenarios and tests stay
# valid): name, email, tier, bookings (code, origin, destination, day offset from today,
# fare, passengers, bags)
PERSONAS = [
    ("Tara Patel", "tara.patel@example.com", "Gold",
     [("5TGTBP", "BLR", "DEL", 9, "Saver", 1, 0), ("8RK2RN", "DEL", "GOI", 31, "Flex", 2, 1)]),
    ("Asha Reddy", "asha.reddy@example.com", "Silver",
     [("S4Z64E", "HYD", "BOM", 12, "Flex", 1, 1)]),
    ("Dev Singh", "dev.singh@example.com", "Blue",
     [("VPGX3T", "DEL", "GOI", 25, "Saver", 1, 0)]),
    ("Kabir Kapoor", "kabir.kapoor@example.com", "Blue",
     [("ZK4M2P", "BOM", "CCU", 18, "Saver", 1, 0)]),
    ("Arjun Das", "arjun.das@example.com", "Blue",
     [("AD7Q3N", "CCU", "BLR", 20, "Saver", 1, 0)]),
    ("Arjun Das", "arjun.das2@example.org", "Silver",
     [("AD9R4K", "MAA", "DEL", 27, "Flex", 1, 1)]),
]


def km(a: str, b: str) -> float:
    """Great-circle distance between two airports (haversine, R = 6371 km)."""
    _, _, la1, lo1, *_ = AIRPORTS[a]
    _, _, la2, lo2, *_ = AIRPORTS[b]
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = p2 - p1, math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def block_minutes(a: str, b: str) -> int:
    return int(5 * round((30 + km(a, b) / 780 * 60) / 5))


def is_intl(a: str, b: str) -> bool:
    return a in INTERNATIONAL or b in INTERNATIONAL


def saver_fare(a: str, b: str, day: date, dep_factor: float) -> int:
    d = km(a, b)
    base = (5500 + 5.2 * d) if is_intl(a, b) else (1400 + 4.1 * d)
    wd = {4: 1.15, 6: 1.15, 5: 1.05}.get(day.weekday(), 1.0)
    out = (day - ANCHOR).days
    adv = 0.85 if out >= 30 else 1.0 if out >= 14 else 1.15 if out >= 7 else 1.35
    return int(round(base * wd * adv * dep_factor / 10) * 10)


def fare_price(saver: int, fare: str) -> int:
    return int(round(saver * FARE_FACTOR[fare] / 10) * 10)


def routes() -> list[tuple[str, str, int]]:
    """(origin, destination, departures per day) for the hub-and-spoke network."""
    out: dict[tuple[str, str], int] = {}
    size = {c: v[4] for c, v in AIRPORTS.items()}
    for i, a in enumerate(HUBS):
        for b in HUBS[i + 1:]:
            n = max(2, min(5, (size[a] + size[b]) // 2 - 1))
            out[(a, b)] = out[(b, a)] = n
    for c in AIRPORTS:
        if c in HUBS or c in INTERNATIONAL:
            continue
        k = {1: 1, 2: 2, 3: 3}.get(size[c], 3)
        hubs = set(sorted(HUBS, key=lambda h: km(c, h))[:k])
        if size[c] >= 3:
            hubs |= {"DEL", "BOM"}       # bigger cities also have the two largest hubs
        for h in sorted(hubs):
            n = max(1, min(3, (size[c] + size[h]) // 3))
            out[(c, h)] = out[(h, c)] = n
    for c, frm in INTL_FROM.items():
        for a in frm:
            out[(a, c)] = out[(c, a)] = 2 if (a in HUBS[:2] and size[c] >= 5) else 1
    return sorted((a, b, n) for (a, b), n in out.items())


def _hhmm(m: int) -> str:
    return f"{m // 60 % 24:02d}:{m % 60:02d}"


@lru_cache(maxsize=1)
def build(seed: int = SEED) -> dict[str, Any]:
    rnd = random.Random(seed)
    rts = routes()
    flights: list[dict[str, Any]] = []
    number = 100
    # one flight number per route and daily slot (same number every day, like a timetable)
    slots = []
    for a, b, n in rts:
        first = rnd.choice([330, 360, 390, 420, 450])       # 05:30 .. 07:30
        span = 22 * 60 - first
        for k in range(n):
            dep = first + (span * k) // max(1, n) + rnd.choice([0, 10, 20, 25, 35])
            number += 1 if number < 999 else -880
            slots.append((a, b, f"SKY{number}", dep))
    for d in range(-PAST_DAYS, DAYS):
        day = ANCHOR + timedelta(days=d)
        for a, b, no, dep in slots:
            dur = block_minutes(a, b)
            h = zlib.crc32(f"{no}:{day.isoformat()}:{seed}".encode())
            dep_factor = 0.95 + (h % 16) / 100
            status = "cancelled_by_airline" if d >= 0 and h % 211 == 0 else "scheduled"
            flights.append({
                "flight_id": f"{no}-{day.strftime('%d%b').upper()}", "flight_no": no,
                "origin": a, "destination": b, "date": day.isoformat(),
                "depart": _hhmm(dep), "arrive": _hhmm(dep + dur),
                "arrive_next_day": dep + dur >= 24 * 60, "duration_min": dur,
                "distance_km": int(round(km(a, b))),
                "seats_left": 0 if h % 97 == 0 else 3 + (h >> 8) % 60,
                "price": saver_fare(a, b, day, dep_factor), "status": status,
                "international": is_intl(a, b)})
    fmap = {f["flight_id"]: f for f in flights}
    # customers: 300 generated + the persona cards; a few shared names on purpose
    customers: list[dict[str, Any]] = []
    used_emails: set[str] = set()
    doms = ["example.com", "example.org", "example.net"]

    def add_customer(name: str, email: str | None = None, tier: str | None = None) -> dict:
        cid = f"C{1001 + len(customers)}"
        if email is None:
            base = name.lower().replace("'", "").replace(" ", ".")
            email, k = f"{base}@{doms[len(customers) % 3]}", 2
            while email in used_emails:
                email, k = f"{base}{k}@{doms[(len(customers) + k) % 3]}", k + 1
        used_emails.add(email)
        intl = name.split()[0] in FIRST_INTL
        phone = (f"+44 7{rnd.randint(100, 999)} {rnd.randint(100000, 999999)}" if intl and
                 rnd.random() < 0.4 else f"+91 {rnd.choice('6789')}{rnd.randint(1000, 9999)} "
                 f"{rnd.randint(10000, 99999)}")
        c = {"customer_id": cid, "name": name, "email": email, "phone": phone,
             "tier": tier or rnd.choice(["Blue", "Blue", "Blue", "Silver", "Gold"])}
        customers.append(c)
        return c
    persona_ids = []
    for name, email, tier, _ in PERSONAS:
        persona_ids.append(add_customer(name, email, tier)["customer_id"])
    names = set()
    while len(customers) < 306:
        if rnd.random() < 0.85:
            nm = f"{rnd.choice(FIRST_IN)} {rnd.choice(LAST_IN)}"
        else:
            nm = f"{rnd.choice(FIRST_INTL)} {rnd.choice(LAST_INTL)}"
        if nm in names and rnd.random() > 0.05:     # mostly unique, a few shared names
            continue
        names.add(nm)
        add_customer(nm)
    # bookings
    bookings: list[dict[str, Any]] = []
    codes: set[str] = set()
    future = [f for f in flights if f["date"] >= ANCHOR.isoformat()]
    past = [f for f in flights if f["date"] < ANCHOR.isoformat()]
    by_route: dict[tuple[str, str], list[dict]] = {}
    for f in flights:
        by_route.setdefault((f["origin"], f["destination"]), []).append(f)

    def new_code() -> str:
        c = ""
        while not c or c in codes:
            c = "".join(rnd.choice(CODE_ALPHABET) for _ in range(6))
        codes.add(c)
        return c

    def booking(code: str, cid: str, segs: list[dict], fare: str, n: int, bags: int,
                status: str) -> dict:
        cust = next(c for c in customers if c["customer_id"] == cid)
        last = cust["name"].split()[-1]
        pax = [cust["name"]] + [f"{rnd.choice(FIRST_IN)} {last}" for _ in range(n - 1)]
        paid = sum(fare_price(s["price"], fare) for s in segs) * n
        b = {"booking_code": code, "customer_id": cid, "flights": [s["flight_id"] for s in segs],
             "passengers": pax, "fare": fare, "bags": bags, "status": status, "paid": paid,
             "currency": CURRENCY}
        if status == "cancelled" and FARE_RULES[fare]["refundable"]:
            b["refunded"] = True
        return b
    for (_name, _e, _t, bks), cid in zip(PERSONAS, persona_ids, strict=True):
        for code, a, b_, off, fare, n, bags in bks:
            day = (ANCHOR + timedelta(days=off)).isoformat()
            seg = min((f for f in by_route[(a, b_)] if f["date"] == day
                       and f["status"] == "scheduled"), key=lambda f: f["depart"])
            codes.add(code)
            bookings.append(booking(code, cid, [seg], fare, n, bags, "confirmed"))
    gen = [c for c in customers if c["customer_id"] not in persona_ids]
    while len(bookings) < 930:
        c = rnd.choice(gen)
        state = rnd.choices(["confirmed", "cancelled", "flown"], [70, 12, 18])[0]
        out = rnd.choice(past if state == "flown" else future)
        segs = [out]
        if rnd.random() < 0.35:          # a return a few days later
            back = [f for f in by_route.get((out["destination"], out["origin"]), [])
                    if 2 <= (date.fromisoformat(f["date"]) - date.fromisoformat(out["date"]))
                    .days <= 10 and (state == "flown") == (f["date"] < ANCHOR.isoformat())]
            if back:
                segs.append(rnd.choice(back))
        fare = rnd.choices(FARES, [60, 28, 12])[0]
        bookings.append(booking(new_code(), c["customer_id"], segs, fare,
                                rnd.choice([1, 1, 1, 2, 2, 3]), rnd.choice([0, 0, 1, 1, 2]),
                                state))
    return {"airline": AIRLINE, "seed": seed, "today": ANCHOR.isoformat(),
            "currency": CURRENCY, "timezone": "IST (UTC+05:30)",
            "airports": {c: {"city": v[0], "name": v[1], "international": c in INTERNATIONAL}
                         for c, v in AIRPORTS.items()},
            "fare_rules": FARE_RULES, "bag_price": BAG_PRICE, "policy": POLICY,
            "offers": OFFERS, "customers": customers, "flights": flights,
            "flight_index": fmap, "bookings": bookings}


def summary() -> dict[str, Any]:
    w = build()
    st: dict[str, int] = {}
    for b in w["bookings"]:
        st[b["status"]] = st.get(b["status"], 0) + 1
    return {"today": w["today"], "airports": len(w["airports"]),
            "indian": sum(1 for a in w["airports"].values() if not a["international"]),
            "routes": len(routes()), "flights": len(w["flights"]),
            "customers": len(w["customers"]), "bookings": len(w["bookings"]),
            "booking_states": st,
            "airline_cancelled": sum(1 for f in w["flights"]
                                     if f["status"] == "cancelled_by_airline")}


if __name__ == "__main__":
    import json
    print(json.dumps(summary(), indent=1))
