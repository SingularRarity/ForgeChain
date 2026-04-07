#!/bin/bash
# Run this once after `docker compose up ollama` to install the ForgeChain models.
# Usage: ./infra/ollama/setup_ollama.sh [ollama-container-name]

CONTAINER=${1:-forgechain-ollama-1}

set -euo pipefail

echo "==> Pulling coding model (qwen2.5-coder:7b)..."
docker exec "$CONTAINER" ollama pull qwen2.5-coder:7b

echo "==> Pulling embedding model (nomic-embed-text)..."
docker exec "$CONTAINER" ollama pull nomic-embed-text

echo "==> Copying Modelfile into container..."
docker cp infra/ollama/Modelfile.forgechain-junior "$CONTAINER":/tmp/Modelfile.forgechain-junior

echo "==> Building forgechain-junior custom model..."
docker exec "$CONTAINER" ollama create forgechain-junior -f /tmp/Modelfile.forgechain-junior

echo "==> Verifying models..."
docker exec "$CONTAINER" ollama list

echo ""
echo "==> Done. Add to .env:"
echo "    LOCAL_LLM_MODEL=forgechain-junior"
echo "    FORGECHAIN_EMBED_MODEL=nomic-embed-text"
echo ""
echo "==> Ingest your first skills file:"
echo "    python -m knowledge.cli ingest file --role backend_dev skills/backend_dev.md"
