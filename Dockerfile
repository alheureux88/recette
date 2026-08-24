# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# ---- deps layer (cached unless pyproject.toml changes) ----
FROM base AS deps
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --frozen

# ---- final image ----
FROM base AS final

# Install system dependencies for file parsing
RUN apt-get update && \
    apt-get install -y --no-install-recommends antiword && \
    rm -rf /var/lib/apt/lists/*

# Copy installed venv from deps stage
COPY --from=deps /app/.venv /app/.venv

# Copy application source and assets
COPY src/       ./src/
COPY static/    ./static/
COPY templates/ ./templates/

# Activate the venv for all subsequent commands
ENV PATH="/app/.venv/bin:$PATH"

RUN mkdir -p /data

EXPOSE 8000

CMD ["uvicorn", "recipes.main:app", "--host", "0.0.0.0", "--port", "8000"]
