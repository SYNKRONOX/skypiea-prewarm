"""Run the predictor service: ``python -m services.predictor`` (PORT, default 8090)."""

from __future__ import annotations

import os

import uvicorn

from services.predictor.app import make_app

if __name__ == "__main__":
    uvicorn.run(make_app(), host=os.environ.get("HOST", "0.0.0.0"),  # noqa: S104 - in-cluster
                port=int(os.environ.get("PORT", "8090")), access_log=False, log_level="warning")
