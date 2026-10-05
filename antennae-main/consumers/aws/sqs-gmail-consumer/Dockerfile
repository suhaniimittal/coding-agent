# Multi-stage build for minimal final image
FROM public.ecr.aws/chainguard/wolfi-base:latest AS builder

RUN apk update && apk upgrade && apk add --no-cache \
    python-3.12 \
    && rm -rf /var/cache/apk/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

# Put venv in-project
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# CodeArtifact Auth
ENV UV_INDEX_CODEARTIFACT_USERNAME=aws

# Copy dependency files
COPY pyproject.toml ./

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=codeartifact_token \
    UV_INDEX_CODEARTIFACT_PASSWORD=$(cat /run/secrets/codeartifact_token) \
    uv sync --no-install-project

# Copy source code
COPY src/ ./src/

# Sync the project
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=secret,id=codeartifact_token \
    UV_INDEX_CODEARTIFACT_PASSWORD=$(cat /run/secrets/codeartifact_token) \
    uv sync

# Final minimal image
FROM public.ecr.aws/chainguard/wolfi-base:latest

# Install runtime dependencies
RUN apk update && apk upgrade && apk add --no-cache \
    python-3.12 \
    tesseract \
    shadow \
    && rm -rf /var/cache/apk/*

# Create a non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser \
    && mkdir -p /home/appuser/.cache/uv \
    && chown -R appuser:appuser /home/appuser

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

# Set environment variables
ENV UV_INDEX_CODEARTIFACT_USERNAME=aws
ENV PYTHONPATH=/app/src
ENV PATH="/app/.venv/bin:$PATH"

# Copy virtual environment from builder
COPY --chown=appuser:appuser --from=builder /app/.venv /app/.venv

# Set working directory and copy application code
WORKDIR /app
COPY --chown=appuser:appuser src/ ./src/
COPY --chown=appuser:appuser pyproject.toml ./

# Ensure downstream images start as root to install packages
ONBUILD USER root

# Switch to non-root user
USER appuser

# Use python to start the application
CMD ["python", "-m", "src.main"]