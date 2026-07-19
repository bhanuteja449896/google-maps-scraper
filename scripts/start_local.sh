#!/bin/bash
# ──────────────────────────────────────────────────────────
# Local development startup script with auto-restart on crash
# ──────────────────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_DIR"

echo "=== Google Maps Scraper API — Local Dev Server ==="
echo "Project: $PROJECT_DIR"
echo ""

# Install dependencies if needed
if [ ! -d ".venv" ] && [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python -m venv .venv
fi

# Activate venv
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

echo "Installing/updating dependencies..."
pip install -q -r requirements.txt

echo ""
echo "Starting API server on http://localhost:8000"
echo "Docs: http://localhost:8000/docs"
echo ""

# Auto-restart loop
RESTART_COUNT=0
while true; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting server (restart #$RESTART_COUNT)..."
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload --log-level info || true
    RESTART_COUNT=$((RESTART_COUNT + 1))
    echo ""
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Server crashed or exited. Restarting in 3 seconds... (Ctrl+C to stop)"
    sleep 3
done
