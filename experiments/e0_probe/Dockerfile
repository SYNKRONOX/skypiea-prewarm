# syntax=docker/dockerfile:1
# E0 in-cluster probe (long-running; driven via kubectl exec). Context: experiments/e0_probe/
# Deliberately on a DIFFERENT base (alpine) than the tool images (debian slim): the probe stays
# on the node, and a shared base would keep the tools' base layers cached, so an "uncached" run
# would not really pull the whole image.
ARG PYTHON_IMAGE=python:3.12-alpine@sha256:e6dae589c1c05976188fd3bd019afd091445fc533ed2ea29001beddc7368a9c8

FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY requirements.txt /req/
RUN /opt/venv/bin/pip install -r /req/requirements.txt

FROM ${PYTHON_IMAGE}
RUN adduser -D -H -u 10001 -s /sbin/nologin app
COPY --from=build /opt/venv /opt/venv
COPY probe.py /app/probe.py
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
WORKDIR /app
CMD ["sleep", "infinity"]
