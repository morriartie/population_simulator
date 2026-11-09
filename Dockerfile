# syntax=docker/dockerfile:1.7
# Multi-stage build (REQ-1B.2). Targets:
#   api   – market REST service (standard library only, no dependencies)
#   sim   – simulation runner and web dashboard (pyyaml, matplotlib)
#   test  – sim + dev dependency group, runs pytest
ARG PYTHON_VERSION=3.12

# ---------------------------------------------------------------- builders
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

# Dependencies only: this layer is reused until pyproject.toml or uv.lock change.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --reinstall-package popsim

FROM builder AS builder-dev
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --reinstall-package popsim

# ---------------------------------------------------------------- runtime
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
RUN groupadd --system popsim \
 && useradd --system --gid popsim --home-dir /app --no-create-home popsim \
 && mkdir -p /app /data \
 && chown popsim:popsim /app /data
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLCONFIGDIR=/tmp/matplotlib

# ------------------------------------------------------------------- api
FROM runtime AS api
COPY --chown=popsim:popsim src/ /app/src/
ENV PYTHONPATH=/app/src \
    POPSIM_DB=/data/popsim_api.sqlite \
    POPSIM_PORT=8080
USER popsim
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=3s --retries=5 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('POPSIM_PORT', '8080'), timeout=3)"]
CMD ["python", "-m", "popsim.market.server", "--host", "0.0.0.0"]

# ------------------------------------------------------------------- sim
FROM runtime AS sim
COPY --from=builder --chown=popsim:popsim /app/.venv /app/.venv
COPY --chown=popsim:popsim scenarios/ /app/scenarios/
RUN mkdir -p /app/runs && chown popsim:popsim /app/runs
USER popsim
EXPOSE 8000
ENTRYPOINT ["popsim"]
CMD ["run", "scenarios/arbitrage.yaml", "--out", "runs/arbitrage"]

# ------------------------------------------------------------------ test
FROM runtime AS test
COPY --from=builder-dev --chown=popsim:popsim /app/.venv /app/.venv
COPY --chown=popsim:popsim pyproject.toml /app/
COPY --chown=popsim:popsim src/ /app/src/
COPY --chown=popsim:popsim scenarios/ /app/scenarios/
COPY --chown=popsim:popsim tests/ /app/tests/
USER popsim
CMD ["pytest", "-q"]
