"""user_lookup (light): find a customer by user_id, email, or name + zip."""

from tools.common.data import load_json
from tools.common.server import burn, create_server, instrument, run

NAME = "user_lookup"
USERS = load_json("users")
BY_ID = {u["user_id"]: u for u in USERS}
BY_EMAIL = {u["email"]: u for u in USERS}

mcp = create_server(NAME, "Look up a customer profile.")


@mcp.tool(name=NAME)
@instrument(NAME)
async def user_lookup(user_id: str = "", email: str = "", name: str = "", zip: str = "",
                      service_ms: int | None = None) -> dict:
    """Find a user by user_id, by email, or by full name and zip code."""
    await burn(service_ms, "cpu")
    user = BY_ID.get(user_id) or BY_EMAIL.get(email)
    if user is None and name:
        user = next((u for u in USERS if u["name"] == name and (not zip or u["zip"] == zip)),
                    None)
    if user is None:  # deterministic stand-in so replayed calls always get a result
        user = USERS[sum(map(ord, user_id + email + name)) % len(USERS)]
    return {"found": True, "user": user}


if __name__ == "__main__":
    run(mcp, NAME, {"users": len(USERS)})
