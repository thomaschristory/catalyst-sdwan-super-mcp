# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

# uv for fast, reproducible installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app
COPY pyproject.toml uv.lock* README.md ./
COPY sdwan_mcp ./sdwan_mcp

# Install into /app/.venv
ENV UV_LINK_MODE=copy
RUN uv sync --frozen --no-dev 2>/dev/null || uv sync --no-dev

# --- runtime ---
FROM python:3.12-slim

WORKDIR /app
COPY --from=builder /app /app
COPY config.yaml ./

ENV PATH="/app/.venv/bin:$PATH"

# Specs are mounted at runtime — not baked into the image
VOLUME ["/app/specs"]

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
# -----------------------------------------------------------------------
