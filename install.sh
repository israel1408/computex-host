#!/bin/bash

# =======================================================
# ComputeX Clearhouse - Authenticated Host Installer
# =======================================================

set -e

echo "⚡ Starting ComputeX Clearhouse Host Node Setup..."

# 1. Hardware Check
if ! command -v nvidia-smi &> /dev/null; then
    echo "❌ Error: nvidia-smi not found. NVIDIA drivers required."
    exit 1
fi

GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n 1)
GPU_VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader | head -n 1)

echo "✅ GPU Detected: $GPU_NAME ($GPU_VRAM)"

# 2. Dependencies
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com -o get-docker.sh && sudo sh get-docker.sh && rm get-docker.sh
fi

if ! command -v tmate &> /dev/null; then
    sudo apt-get update -y && sudo apt-get install -y tmate
fi

# 3. Configuration
NODE_ID="node-$(head /dev/urandom | tr -dc a-z0-9 | head -c 6)"
RENDER_BOT_URL="https://computex-bot.onrender.com"
HOST_API_KEY="computex-secret-host-key-2026"

echo "🆔 Assigned Node ID: $NODE_ID"

# 4. Authenticated Registration
echo "🌐 Authenticating and registering node..."
REGISTER_PAYLOAD=$(cat <<EOF
{
  "node_id": "$NODE_ID",
  "gpu_name": "$GPU_NAME",
  "vram": "$GPU_VRAM",
  "hourly_rate": 0.30
}
EOF
)

RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$RENDER_BOT_URL/register-node" \
     -H "Content-Type: application/json" \
     -H "X-Host-API-Key: $HOST_API_KEY" \
     -d "$REGISTER_PAYLOAD")

if [ "$RESPONSE" -eq 200 ]; then
    echo "🎉 ComputeX Host Node Authenticated & Online!"
else
    echo "❌ Registration failed! Server responded with HTTP status $RESPONSE."
    exit 1
fi

# 5. Background Heartbeat Service
echo "🔄 Starting node heartbeat service..."
nohup bash -c "
while true; do
  curl -s -X POST '$RENDER_BOT_URL/heartbeat' \
       -H 'Content-Type: application/json' \
       -H 'X-Host-API-Key: $HOST_API_KEY' \
       -d '{\"node_id\": \"$NODE_ID\"}' > /dev/null
  sleep 45
done
" > /dev/null 2>&1 &

echo "✅ Setup complete. Host node is listening for rental workloads."
