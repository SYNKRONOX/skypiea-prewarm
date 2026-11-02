# syntax=docker/dockerfile:1
# run_tests: heavy tool. Generated from the tier template; build with tools/build_images.sh
# (context = tools/).
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337

FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY common/requirements-light.txt common/requirements-heavy.txt common/constraints.txt /req/
RUN /opt/venv/bin/pip install -r /req/requirements-heavy.txt -c /req/constraints.txt

# Train the model at build time (seeded, deterministic); only model.joblib reaches the image.
COPY __init__.py /src/tools/__init__.py
COPY common/ /src/tools/common/
COPY run_tests/ /src/tools/run_tests/
RUN cd /src && /opt/venv/bin/python -m tools.run_tests.train /opt/model.joblib

FROM ${PYTHON_IMAGE}
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/model.joblib /app/model.joblib
WORKDIR /app
COPY __init__.py tools/__init__.py
COPY common/ tools/common/
COPY run_tests/ tools/run_tests/
RUN python -m compileall -q tools
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PORT=8080 SKYPIEA_TOOL=run_tests SKYPIEA_TIER=heavy \
    SKYPIEA_MODEL=/app/model.joblib
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "tools.run_tests.server"]
