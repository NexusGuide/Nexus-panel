ARG PYTHON_VERSION=3.14

FROM ghcr.io/astral-sh/uv:python$PYTHON_VERSION-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    libc6-dev \
    && rm -rf /var/lib/apt/lists/*

ENV UV_PYTHON_DOWNLOADS=0

WORKDIR /build
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev
ADD . /build
# Compile every module before anything else. This has caught a whole tree of
# `except A, B:` handlers - Python 2 syntax that no longer parses - twice now,
# once in the sources this fork started from and once from a formatter that
# rewrote them. Both times the failure would otherwise have surfaced as a
# crash-looping container after the image was already published.
RUN python -m compileall -q app cli scripts config.py main.py
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# The dashboard has to be compiled here now.
#
# It used to be lifted wholesale out of upstream's published image, which was
# right while this fork changed no dashboard source: the official build was
# exactly the right one, and it kept bun out of the build entirely. Adding the
# Free Configs nav entry and route ends that - upstream's compiled bundle does
# not contain them - so the fork compiles its own.
#
# --platform=$BUILDPLATFORM: the output is static JS and CSS, identical on every
# architecture, so compile once on the builder's own platform instead of running
# the whole toolchain again under emulation for the arm64 leg.
FROM --platform=$BUILDPLATFORM oven/bun:1-slim AS dashboard_src
WORKDIR /code/dashboard
# dependencies first, so editing a component does not re-resolve the lockfile
COPY dashboard/package.json dashboard/bun.lock ./
RUN bun install --frozen-lockfile
COPY dashboard/ ./
# VITE_BASE_API=/ matches upstream's build_dashboard.sh: the panel serves the
# dashboard and the API from the same origin. 404.html is the SPA fallback.
RUN VITE_BASE_API=/ bun run build && cp ./build/index.html ./build/404.html

FROM python:$PYTHON_VERSION-slim-bookworm

COPY --from=builder /build /code
COPY --from=dashboard_src /code/dashboard/build /code/dashboard/build
WORKDIR /code

ENV PATH="/code/.venv/bin:$PATH"

# Install curl for health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Two names for each entry point on purpose: upstream's `pasarguard` command
# execs the pasarguard-* ones by name, so removing them would break `nexus cli`,
# while the nexus-* ones are what someone gets a shell in the container expects.
COPY cli_wrapper.sh /usr/bin/pasarguard-cli
RUN chmod +x /usr/bin/pasarguard-cli && ln -s /usr/bin/pasarguard-cli /usr/bin/nexus-cli

COPY tui_wrapper.sh /usr/bin/pasarguard-tui
RUN chmod +x /usr/bin/pasarguard-tui && ln -s /usr/bin/pasarguard-tui /usr/bin/nexus-tui

# Copy healthcheck script
COPY healthcheck.sh /code/healthcheck.sh
RUN chmod +x /code/healthcheck.sh

RUN chmod +x /code/start.sh

ENTRYPOINT ["/code/start.sh"]
