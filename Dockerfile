# ─────────────────────────────────────────────────────────────────────────────
# Google Maps Scraper API — Cloud Run Dockerfile
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Make startup script executable
RUN chmod +x scripts/entrypoint.sh

# Cloud Run injects PORT (default 8080). Keep PYTHONUNBUFFERED so logs stream immediately.
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PORT=8080

# Expose the port (informational — Cloud Run reads PORT env var)
EXPOSE 8080

# Health check (Cloud Run uses /health by default if configured)
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# Start the API
CMD ["sh", "scripts/entrypoint.sh"]
