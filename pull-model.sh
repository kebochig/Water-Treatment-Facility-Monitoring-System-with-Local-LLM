#!/bin/bash
set -e

echo "⏳ Waiting for Ollama to be ready..."

# Wait until Ollama is up
until curl -s http://ollama:11434/api/tags >/dev/null; do
  echo "🔄 Ollama not ready yet. Sleeping..."
  sleep 3
done

echo "✅ Ollama is up. Pulling model..."

curl -X POST http://ollama:11434/api/pull \
  -H "Content-Type: application/json" \
  -d '{"name": "llama3.2:1b"}'

echo "✅ Model pulled successfully"