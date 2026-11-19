# syntax=docker/dockerfile:1
# search: medium tool. Generated from the tier template; build with tools/build_images.sh
# (context = tools/).
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY common/requirements-light.txt common/requirements-medium.txt common/constraints.txt /req/
RUN /opt/venv/bin/pip install -r /req/requirements-medium.txt -c /req/constraints.txt

FROM ${PYTHON_IMAGE}
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY __init__.py tools/__init__.py
COPY common/ tools/common/
COPY search/ tools/search/
RUN python -m compileall -q tools
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PORT=8080 SKYPIEA_TOOL=search SKYPIEA_TIER=medium
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "tools.search.server"]
