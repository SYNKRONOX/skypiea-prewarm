# controller

Owner: Ch. Dhanunjaya

A [kopf](https://kopf.readthedocs.io/) operator that scales MCP tool Deployments in the
`skypiea-tools` namespace between 0 and N replicas. It applies `WarmDecision`s from the
predictor service and scales idle tools back to zero. We use plain Deployments because
OpenFaaS CE cannot scale to zero.

Status: not started.
