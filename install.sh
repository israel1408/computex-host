#!/bin/bash

# =======================================================
# ComputeX Clearhouse - Host Node Auto-Installer
# =======================================================

set -e

echo "⚡ Starting ComputeX Clearhouse Host Node Setup..."

# 1. Check for NVIDIA Driver & GPU
if ! command -v nvidia-smi &> /dev/null; then
    echo "❌ Error: nvidia-smi not found. Please install NVIDIA drivers first."
    exit 1
fi

GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n 1)
GPU_VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader | head -n 1)

echo "✅ GPU Detected: $GPU_NAME ($GPU_VRAM)"

# 2. Install Docker & tmate if missing
echo "📦 Checking dependencies (Docker, tmate, curl)..."
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    rm get-docker.sh
fi

if ! command -v tmate &> /dev/null; then
    echo "Installing tmate for reverse SSH tunnels..."
    sudo apt-get update -y && sudo apt-get install -y tmate
fi

# 3. Generate or Retrieve Unique Node ID
NODE_ID="node-$(head /dev/urandom | tr -dc a-z0-9 | head -c 6)"
echo "🆔 Assigned Node ID: $NODE_ID"

# 4. Prompt for Central Bot Endpoint
RENDER_BOT_URL="https://<your-render-app-name>.onrender.com"

# 5. Register Node with Central Bot
echo "🌐 Registering node with ComputeX Clearhouse Network..."
REGISTER_PAYLOAD=$(cat <<EOF
{
  "node_id": "$NODE_ID",
  "gpu_name": "$GPU_NAME",
  "vram": "$GPU_VRAM",
  "hourly_rate": 0.30,
  "status": "ONLINE"
}
EOF
)

curl -X POST "$RENDER_BOT_URL/register-node" \
     -H "Content-Type: application/json" \
     -d "$REGISTER_PAYLOAD"

echo ""
echo "======================================================="
echo "🎉 ComputeX Host Node Successfully Installed & Online!"
echo "Node ID: $NODE_ID"
echo "Status: Listening for incoming user rentals..."
echo "======================================================="
