#!/bin/bash
# Run this once after `docker compose up ollama` to install the ForgeChain models.
# Usage: ./infra/ollama/setup_ollama.sh [ollama-container-name]

CONTAINER=${1:-forgechain-ollama-1}

set -euo pipefail
echo "==> Pulling base model..."
docker exec "$CONTAINER" ollama pull qwen2.5-coder:7b

echo "==> Copying Modelfile into container..."
docker cp infra/ollama/Modelfile.forgechain-junior "$CONTAINER":/tmp/Modelfile.forgechain-junior

echo "==> Building forgechain-junior custom model..."
docker exec "$CONTAINER" ollama create forgechain-junior -f /tmp/Modelfile.forgechain-junior

echo "==> Verifying..."
docker exec "$CONTAINER" ollama list | grep forgechain

echo "==> Done. Set LOCAL_LLM_MODEL=forgechain-junior in your .env"
