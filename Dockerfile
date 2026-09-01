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
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:$PYTHON_VERSION-slim-bookworm

COPY --from=builder /build /code
WORKDIR /code

# CI compiles the dashboard into dashboard/build before the image is built (see
# .github/workflows/build-fork.yml). A plain `docker build` from a source
# checkout has no such directory, and the panel then shells out to `bun` at
# startup - which is not in this image - so it dies with
# `FileNotFoundError: 'bun'` and the container crash-loops.
# A placeholder keeps that path from ever running: build() is skipped because
# the directory exists, both StaticFiles mounts resolve, and the API comes up.
# When CI supplies a real dashboard, index.html is already there and this is a
# no-op.
RUN mkdir -p /code/dashboard/build/statics && \
    if [ ! -f /code/dashboard/build/index.html ]; then \
        printf '%s' '<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>PasarGuard</title><style>body{font-family:system-ui,sans-serif;margin:0;min-height:100vh;display:grid;place-items:center;background:#0f1115;color:#e6e8ec}main{max-width:34rem;padding:2rem}h1{margin:0 0 .5rem;font-size:1.35rem}p{color:#9aa1ac;line-height:1.6}a{color:#4f8cff}code{background:#1b1f27;padding:.15rem .4rem;border-radius:4px}</style></head><body><main><h1>Panel is running</h1><p>The web dashboard was not compiled into this image &mdash; that happens in CI, and a plain <code>docker build</code> from source skips it.</p><p>The API is fully available at <a href="/docs">/docs</a>, free-configs endpoints included.</p></main></body></html>' \
            > /code/dashboard/build/index.html && \
        cp /code/dashboard/build/index.html /code/dashboard/build/404.html; \
    fi

ENV PATH="/code/.venv/bin:$PATH"

# Install curl for health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY cli_wrapper.sh /usr/bin/pasarguard-cli
RUN chmod +x /usr/bin/pasarguard-cli

COPY tui_wrapper.sh /usr/bin/pasarguard-tui
RUN chmod +x /usr/bin/pasarguard-tui

# Copy healthcheck script
COPY healthcheck.sh /code/healthcheck.sh
RUN chmod +x /code/healthcheck.sh

RUN chmod +x /code/start.sh

ENTRYPOINT ["/code/start.sh"]
