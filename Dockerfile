# =============================================================================
# Dockerfile for PropTech Intake API
# Multi-stage build for minimal production image
# =============================================================================

FROM python:3.11-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies (curl for healthcheck)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --create-home appuser

WORKDIR /app

# Switch to non-root user early for zero-trust package installation
USER appuser

# Add local pip bin to PATH
ENV PATH="/home/appuser/.local/bin:${PATH}"

# Install Python dependencies
COPY --chown=appuser:appuser src/intake-api/requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# Copy application code with correct ownership
COPY --chown=appuser:appuser src/intake-api/ .

# Expose the application port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Run with gunicorn - 4 workers, 4 threads per worker
CMD ["gunicorn", "--workers", "4", "--threads", "4", "--bind", "0.0.0.0:8080", "--timeout", "30", "--access-logfile", "-", "app:app"]
