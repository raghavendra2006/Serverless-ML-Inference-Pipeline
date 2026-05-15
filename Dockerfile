# =============================================================================
# Dockerfile for PropTech Intake API
# Multi-stage build for minimal production image
# =============================================================================

FROM python:3.11-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies for python-magic (libmagic)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libmagic1 \
        curl && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --create-home appuser

WORKDIR /app

# Install Python dependencies
COPY src/intake-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/intake-api/ .

# Switch to non-root user
USER appuser

# Expose the application port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Run with gunicorn - 4 workers, 4 threads per worker
CMD ["gunicorn", "--workers", "4", "--threads", "4", "--bind", "0.0.0.0:8080", "--timeout", "30", "--access-logfile", "-", "app:app"]
