# shaper

Owner: Ch. Dhanunjaya

The request shaper sits between the replayer (or a real agent) and the MCP tool pods. It:

- forwards each tool call to its pod, holding a call for up to 150 ms if a warm pod is about to be ready
- reports every call (metadata only) to the predictor service
- exports latency and cold-start metrics to Prometheus

Status: not started.
