# tools

Owner: Ch. Dhanunjaya

Container images for the 12 to 15 MCP tool classes (see `data/README.md`). Each tool is a
small MCP server whose service time follows the measured distribution from the traces, so
cold-start cost is realistic without calling real external APIs.

Status: done. 16 tool servers, one folder each, mapped from tool classes by
[`tool_map.yaml`](tool_map.yaml) (the planned count above was 12 to 15). How to add one:
[`dev.md`](../dev.md), "How to add things".
