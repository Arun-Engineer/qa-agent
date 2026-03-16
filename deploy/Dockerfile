# ═══════════════════════════════════════════════════
#  AI QA Platform — Production Dockerfile
#  Target: AWS EC2 (t2.micro / t3.micro free tier)
#  DB: SQLite (data/ volume persisted)
# ═══════════════════════════════════════════════════

FROM python:3.11-slim AS base

# System deps for Playwright, bcrypt, SQLite
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gcc g++ libffi-dev libssl-dev \
    sqlite3 \
    # Playwright system deps
    libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    libxshmfence1 libxrandr2 libxcomposite1 libxdamage1 \
    && rm -rf /var/lib/apt/lists/*

# App user (non-root)
RUN useradd -m -s /bin/bash appuser

WORKDIR /app

# Install Python deps first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir gunicorn uvicorn[standard]

# Install Playwright browsers
RUN pip install --no-cache-dir playwright && \
    playwright install chromium && \
    playwright install-deps chromium

# Copy application code
COPY . .

# Create data directory for SQLite (will be volume-mounted)
RUN mkdir -p /app/data /app/logs /app/reports && \
    chown -R appuser:appuser /app

# Config directory
RUN mkdir -p /app/config && \
    chown -R appuser:appuser /app/config

USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

# Production server: gunicorn with uvicorn workers
# 2 workers for t2.micro (1GB RAM), scale up for larger instances
CMD ["gunicorn", "asgi:app", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
