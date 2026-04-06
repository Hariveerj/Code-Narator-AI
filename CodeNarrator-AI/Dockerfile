# ── CodeNarrator-AI ──────────────────────────────────────────────────────────
# Multi-stage build: slim Python image serving FastAPI + static frontend.
# Expects Ollama to be available at OLLAMA_BASE_URL (default: host network).
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.14-slim AS base

# Prevents .pyc files and enables unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY scripts/ ./scripts/
COPY app.py .

EXPOSE 8081

# Health check against the built-in /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8081/health').raise_for_status()"

CMD ["python", "app.py"]
