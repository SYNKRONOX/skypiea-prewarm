# syntax=docker/dockerfile:1
# Skypiea platform image: gateway, controller (kopf) and the predictor placeholder.
# Build from the repo root:  docker build -f deploy/platform/Dockerfile .
# (deploy/platform/Dockerfile.dockerignore keeps the context to the few files needed)
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337

FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY deploy/platform/requirements.txt deploy/platform/constraints.txt /req/
RUN /opt/venv/bin/pip install -r /req/requirements.txt -c /req/constraints.txt

FROM ${PYTHON_IMAGE}
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY tools/__init__.py tools/__init__.py
COPY tools/common/__init__.py tools/common/toolmap.py tools/common/
COPY tools/tool_map.yaml tools/tool_map.yaml
COPY configs/cold_start.yaml configs/cold_start.yaml
COPY gateway/ gateway/
COPY controller/ controller/
RUN python -m compileall -q tools gateway controller
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
USER 10001:10001
EXPOSE 8080 8081
CMD ["python", "-m", "gateway.main", "gateway"]
