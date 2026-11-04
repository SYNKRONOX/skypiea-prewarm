"""Run the predictor service: ``python -m services.predictor`` (PORT 8090, WORKERS 1)."""

from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("services.predictor.app:make_app", factory=True,
                host=os.environ.get("HOST", "0.0.0.0"),  # noqa: S104 - in-cluster service
                port=int(os.environ.get("PORT", "8090")),
                workers=int(os.environ.get("WORKERS", "1")), access_log=False,
                log_level="warning")
