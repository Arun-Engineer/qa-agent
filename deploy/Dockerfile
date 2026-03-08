# deploy/Dockerfile — Multi-stage production build for AI QA Platform
# Stage 1: Build dependencies
# Stage 2: Slim runtime image

# ═══════════════════════════════════
# Stage 1: Build
# ═══════════════════════════════════
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps for building Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ═══════════════════════════════════
# Stage 2: Runtime
# ═══════════════════════════════════
FROM python:3.11-slim

LABEL maintainer="AI QA Platform"
LABEL version="5.0.0"

WORKDIR /app

# System deps for runtime (Playwright browsers, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY . .

# Create data directories
RUN mkdir -p data/logs data/chroma data/runs

# Non-root user for security
RUN groupadd -r aiqa && useradd -r -g aiqa -d /app aiqa \
    && chown -R aiqa:aiqa /app/data
USER aiqa

# Environment defaults (override via ECS task def or docker-compose)
ENV APP_ENV=production \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

EXPOSE ${PORT}

# Run with uvicorn
CMD ["python", "-m", "uvicorn", "asgi:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
