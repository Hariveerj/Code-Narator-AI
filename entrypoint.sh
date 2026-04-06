#!/bin/bash
set -e

MODEL="${OLLAMA_MODEL:-llama3.2:3b}"

echo "▶ Starting Ollama server..."
ollama serve &
OLLAMA_PID=$!

# Wait for Ollama to become ready
echo "⏳ Waiting for Ollama to be ready..."
for i in $(seq 1 30); do
  if curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "✅ Ollama is ready."
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "❌ Ollama did not start in time."
    exit 1
  fi
  sleep 2
done

# Pull the model if not already present
if ! ollama list | grep -q "${MODEL%%:*}"; then
  echo "⬇ Pulling model $MODEL (this may take a while on first run)..."
  ollama pull "$MODEL"
  echo "✅ Model $MODEL pulled."
else
  echo "✅ Model $MODEL already available."
fi

echo "▶ Starting CodeNarrator-AI app..."
exec python app.py
