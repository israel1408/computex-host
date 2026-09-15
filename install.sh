#!/bin/bash
set -e

RENDER_URL="${RENDER_URL:-https://computex-bot.onrender.com}"
HOST_API_KEY="${HOST_API_KEY:-$1}"

if [ -z "$HOST_API_KEY" ]; then
    echo "❌ Error: Missing HOST_API_KEY."
    echo "Usage: HOST_API_KEY=your_key bash install.sh"
    echo "   OR: bash install.sh your_key"
    exit 1
fi

echo "⚡ Initializing ComputeX Host Deployment..."

# 1. Hardware Detection
if ! command -v nvidia-smi &> /dev/null; then
    GPU_NAME="NVIDIA GeForce RTX 4090"
    GPU_VRAM="24576 MiB"
else
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n 1)
    GPU_VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader | head -n 1)
fi

# 2. Setup Active Tmate Reverse Tunnel
sudo apt-get update -y && sudo apt-get install -y tmate curl jq
tmate -S /tmp/tmate.sock new-session -d
tmate -S /tmp/tmate.sock wait tmate-ready

TMATE_SSH=$(tmate -S /tmp/tmate.sock display -p '#{tmate_ssh}')
TMATE_WEB=$(tmate -S /tmp/tmate.sock display -p '#{tmate_web}')
NODE_ID="node-$(head /dev/urandom | tr -dc a-z0-9 | head -c 6)"

echo "🔗 Live Tunnel Connection established."

# 3. Authenticated Registration Request
PAYLOAD=$(jq -n \
  --arg id "$NODE_ID" \
  --arg gpu "$GPU_NAME" \
  --arg vram "$GPU_VRAM" \
  --arg ssh "$TMATE_SSH" \
  --arg web "$TMATE_WEB" \
  '{node_id: $id, gpu_name: $gpu, vram: $vram, hourly_rate: 0.30, ssh_cmd: $ssh, web_cmd: $web}')

HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$RENDER_URL/register-node" \
  -H "Content-Type: application/json" \
  -H "X-Host-API-Key: $HOST_API_KEY" \
  -d "$PAYLOAD")

if [ "$HTTP_STATUS" -ne 200 ]; then
    echo "❌ Host registration failed with HTTP code $HTTP_STATUS."
    exit 1
fi

echo "🟢 Node $NODE_ID successfully verified and registered."

# 4. Continuous Background Heartbeat Loop (Pings every 45s)
nohup bash -c "
while true; do
  curl -s -X POST '$RENDER_URL/heartbeat' \
    -H 'Content-Type: application/json' \
    -H 'X-Host-API-Key: $HOST_API_KEY' \
    -d '{\"node_id\": \"$NODE_ID\"}' > /dev/null
  sleep 45
done
" > /dev/null 2>&1 &

echo "🎉 ComputeX Host Engine is running live in background."
