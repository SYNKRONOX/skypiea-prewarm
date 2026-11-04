# syntax=docker/dockerfile:1
# Skypiea predictor service. Build from the repo root, with a bundle from
# `python -m services.predictor.export` (models/ is not in git):
#   docker build -f services/predictor/Dockerfile --build-arg MODEL_DIR=models/<version> .
# Run read-only: docker run --read-only -e COLD_START_PATH=/cold/cold_start.yaml \
#   -v <dir with cold_start.yaml>:/cold:ro -p 8090:8090 <image>
# (services/predictor/Dockerfile.dockerignore keeps the context to these folders.)
# Same pinned base image as the platform image (feat/infra deploy/platform/Dockerfile).
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337

FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY services/predictor/requirements.txt services/predictor/requirements.lock /req/
RUN /opt/venv/bin/pip install -r /req/requirements.txt -c /req/requirements.lock

FROM ${PYTHON_IMAGE}
ARG MODEL_DIR
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY predictors/ predictors/
COPY traces/ traces/
COPY configs/model/ configs/model/
COPY services/predictor/ services/predictor/
COPY ${MODEL_DIR}/ /models/current/
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/* \
 && python -m compileall -q predictors traces services \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
 && rm -f /usr/bin/apt* /usr/bin/dpkg* /usr/bin/perl* /bin/bash /usr/bin/bash /bin/dash \
          /usr/bin/dash /bin/sh /usr/bin/sh
ENV PATH=/opt/venv/bin:$PATH PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PORT=8090 MODEL_PATH=/models/current MODEL_ROOT=/models
USER 10001:10001
EXPOSE 8090
CMD ["python", "-m", "services.predictor"]
