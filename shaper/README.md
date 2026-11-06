# shaper

Owner: Ch. Dhanunjaya

The request shaper sits between the replayer (or a real agent) and the MCP tool pods. It:

- forwards each tool call to its pod, holding a call for up to 150 ms if a warm pod is about to be ready
- reports every call (metadata only) to the predictor service
- exports latency and cold-start metrics to Prometheus

Status: superseded. The shaper was built as part of the gateway (`gateway/`: the 150 ms hold,
metadata reports to the predictor, Prometheus metrics). See [`dev.md`](../dev.md) and
[`docs/gateway_controller.md`](../docs/gateway_controller.md).
