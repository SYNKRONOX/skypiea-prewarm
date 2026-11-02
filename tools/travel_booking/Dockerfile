# syntax=docker/dockerfile:1
# travel_booking: medium tool. Generated from the tier template; build with tools/build_images.sh
# (context = tools/).
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337

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
COPY travel_booking/ tools/travel_booking/
RUN python -m compileall -q tools
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PORT=8080 SKYPIEA_TOOL=travel_booking SKYPIEA_TIER=medium
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "tools.travel_booking.server"]
