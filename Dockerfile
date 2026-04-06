# ── CodeNarrator-AI ──────────────────────────────────────────────────────────
# Single-container build: Ollama + FastAPI app in one image.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

# Prevents .pyc files and enables unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install curl (needed by Ollama installer and healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install Ollama
RUN curl -fsSL https://ollama.com/install.sh | sh

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY scripts/ ./scripts/
COPY app.py .
COPY entrypoint.sh .
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh

# Ollama talks on 11434 internally; app on 8081
ENV OLLAMA_BASE_URL=http://localhost:11434 \
    OLLAMA_MODEL=llama3.2:3b \
    HOST=0.0.0.0 \
    PORT=8081

EXPOSE 8081

# Health check against the built-in /health endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
    CMD curl -sf http://localhost:8081/health || exit 1

ENTRYPOINT ["./entrypoint.sh"]
