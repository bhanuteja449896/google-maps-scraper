#!/bin/sh
# Cloud Run / Docker entrypoint
# Cloud Run sets PORT automatically (default 8080)
set -e
exec uvicorn api.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8080}" \
    --workers 1 \
    --log-level info \
    --timeout-keep-alive 120
