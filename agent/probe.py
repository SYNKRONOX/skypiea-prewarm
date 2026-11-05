"""Call a few tools through the gateway's POST /mcp and report each call (failure injection).

    python -m agent.probe --tools order_lookup,policy_check,refund_issue
Exit 1 if any call failed.
"""

from __future__ import annotations

import argparse

from agent.mcp_client import McpClient


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gateway", default="http://127.0.0.1:18080/mcp")
    ap.add_argument("--tools", default="order_lookup,policy_check,refund_issue")
    ap.add_argument("--timeout-s", type=float, default=180)
    a = ap.parse_args(argv)
    c = McpClient(a.gateway, timeout_s=a.timeout_s)
    bad = 0
    try:
        for t in a.tools.split(","):
            _, info = c.call_tool(t, {})
            bad += not info.ok
            print(f"  {t:14s} ok={info.ok} cold={info.cold} added_ms={info.added_ms} "
                  f"wall_ms={info.wall_ms:.0f}" + (f" error={info.error}" if info.error else ""),
                  flush=True)
    finally:
        c.close()
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
