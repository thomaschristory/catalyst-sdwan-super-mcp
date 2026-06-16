# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

# uv for fast, reproducible installs. Pinned to an immutable version + digest
# (#55 L1/L4) so the build toolchain can't drift onto an unreviewed upstream
# tag. Bump deliberately (Dependabot tracks the version comment).
COPY --from=ghcr.io/astral-sh/uv:0.11.21@sha256:ff07b86af50d4d9391d9daf4ff89ce427bc544f9aae87057e69a1cc0aa369946 /uv /uvx /usr/local/bin/

WORKDIR /app
# Copy uv.lock explicitly (not a glob) so a missing lockfile is a hard error.
COPY pyproject.toml uv.lock README.md ./
COPY sdwan_mcp ./sdwan_mcp

# Install into /app/.venv. --frozen is fail-closed (#55 L2): no fallback to an
# unpinned re-resolution, and no 2>/dev/null swallowing why a frozen build failed.
ENV UV_LINK_MODE=copy
RUN uv sync --frozen --no-dev

# --- runtime ---
FROM python:3.12-slim

# Run as an unprivileged user (#55 L5) — limits blast radius for the
# network-facing SSE/HTTP transports. Created before the copy so /app is owned
# by the runtime uid; the mounted /app/specs volume stays readable.
RUN useradd --system --uid 10001 --create-home --home-dir /home/app app

WORKDIR /app
COPY --from=builder --chown=10001:10001 /app /app
COPY --chown=10001:10001 sdwan-mcp.yaml ./

ENV PATH="/app/.venv/bin:$PATH"

# Specs are mounted at runtime — not baked into the image
VOLUME ["/app/specs"]

USER app

ENTRYPOINT ["sdwan-mcp"]
CMD []

# -----------------------------------------------------------------------
# Usage:
#
# Build:
#   docker build -t catalyst-sdwan-super-mcp .
#
# Claude Desktop (stdio):
#   docker run -i --rm \
#     -e VMANAGE_USERNAME=admin \
#     -e VMANAGE_PASSWORD=secret \
#     -v $(pwd)/specs:/app/specs \
#     catalyst-sdwan-super-mcp
#
# Network (SSE):
#   docker run -p 8000:8000 \
#     -e VMANAGE_USERNAME=admin \
#     -e VMANAGE_PASSWORD=secret \
#     -v $(pwd)/specs:/app/specs \
#     catalyst-sdwan-super-mcp --transport sse --host 0.0.0.0 --port 8000
#
# Network (streamable-http):
#   docker run -p 8000:8000 \
#     -e VMANAGE_USERNAME=admin \
#     -e VMANAGE_PASSWORD=secret \
#     -v $(pwd)/specs:/app/specs \
#     catalyst-sdwan-super-mcp --transport streamable-http --host 0.0.0.0 --port 8000
# -----------------------------------------------------------------------
