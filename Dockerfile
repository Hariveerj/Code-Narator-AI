# ── CodeNarrator-AI ──────────────────────────────────────────────────────────
# Single-container build: Ollama + FastAPI app in one image.
# Security: non-root user, pinned Ollama version, minimal attack surface.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

# Prevents .pyc files and enables unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Pin Ollama version for reproducible builds
ARG OLLAMA_VERSION=0.6.2

# Install system deps + Ollama (pinned version) in a single layer, then clean up
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    curl -fsSL "https://github.com/ollama/ollama/releases/download/v${OLLAMA_VERSION}/ollama-linux-amd64.tgz" \
      -o /tmp/ollama.tgz && \
    tar -xzf /tmp/ollama.tgz -C /usr && \
    rm -f /tmp/ollama.tgz

# Create non-root user for running the application
RUN groupadd --system appgroup && \
    useradd --system --gid appgroup --create-home --home-dir /home/appuser appuser && \
    mkdir -p /home/appuser/.ollama && \
    chown -R appuser:appgroup /home/appuser/.ollama

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
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh && \
    chown -R appuser:appgroup /app

# Ollama talks on 11434 internally; app on 8081
ENV OLLAMA_BASE_URL=http://localhost:11434 \
    OLLAMA_MODEL=llama3.2:3b \
    OLLAMA_HOME=/home/appuser/.ollama \
    HOST=0.0.0.0 \
    PORT=8081

EXPOSE 8081

# Switch to non-root user
USER appuser

# Health check against the built-in /health endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
    CMD curl -sf http://localhost:8081/health || exit 1

ENTRYPOINT ["./entrypoint.sh"]
