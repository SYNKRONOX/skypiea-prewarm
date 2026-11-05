"""travel_booking (medium): flight search and reservation actions over pandas tables."""

import pandas as pd

from tools.common.data import SUPPORT_DIR
from tools.common.server import burn, create_server, instrument, run

NAME = "travel_booking"
FLIGHTS = pd.read_json(SUPPORT_DIR / "flights.json")
RES = pd.read_json(SUPPORT_DIR / "reservations.json").set_index("reservation_id", drop=False)
ROUTES = FLIGHTS.groupby(["origin", "destination"]).size()
BY_FLIGHT = FLIGHTS.drop_duplicates("flight_no").set_index("flight_no", drop=False)

mcp = create_server(NAME, "Search flights and manage reservations.")


@mcp.tool(name=NAME)
@instrument(NAME)
async def travel_booking(action: str = "search", origin: str = "BLR", destination: str = "DEL",
                         day: int = 0, reservation_id: str = "", detail: bool = False,
                         flight_no: str = "", service_ms: int | None = None) -> dict:
    """action: search | lookup | modify | cancel | book.
    detail=true (live console demo; default off): lookup/modify/cancel also return the owner
    and the flight's route and schedule; modify/book take the new flight_no."""
    await burn(service_ms, "cpu")
    if action == "search":
        f = FLIGHTS[(FLIGHTS["origin"] == origin) & (FLIGHTS["destination"] == destination)]
        if day:
            f = f[f["day"] == day]
        f = f[f["seats_left"] > 0].sort_values(["price", "flight_no"]).head(5)
        return {"action": action, "origin": origin, "destination": destination,
                "flights": f[["flight_no", "day", "depart", "price", "cabin"]].to_dict("records")}
    if reservation_id not in RES.index:
        reservation_id = RES.index[sum(map(ord, reservation_id)) % len(RES)]
    r = RES.loc[reservation_id]
    out = {"action": action, "reservation_id": reservation_id, "flight_no": r["flight_no"],
           "cabin": r["cabin"], "passengers": int(r["passengers"]), "bags": int(r["bags"])}
    if action in ("modify", "cancel"):
        out["accepted"] = not (action == "modify" and r["cabin"] == "basic_economy")
    elif action == "book":
        out["accepted"] = True
    if detail:
        out["user_id"] = r["user_id"]
        if r["flight_no"] in BY_FLIGHT.index:
            f = BY_FLIGHT.loc[r["flight_no"]]
            out.update(origin=f["origin"], destination=f["destination"], day=int(f["day"]),
                       depart=f["depart"])
        if action in ("modify", "book") and flight_no:
            out["new_flight_no"] = flight_no
            if flight_no in BY_FLIGHT.index:
                g = BY_FLIGHT.loc[flight_no]
                out.update(new_day=int(g["day"]), new_depart=g["depart"],
                           new_origin=g["origin"], new_destination=g["destination"])
    return out


if __name__ == "__main__":
    run(mcp, NAME, {"flights": len(FLIGHTS), "routes": len(ROUTES)})
