#!/bin/bash

# ==========================================
# ComputeX Clearhouse Host Node Installer
# ==========================================

set -e

echo "⚡ Starting ComputeX Host Node Installation..."

# 1. Check for root / sudo
if [ "$EUID" -ne 0 ]; then
  echo "❌ Please run as root or using sudo: sudo bash install.sh"
  exit 1
fi

# 2. Check for NVIDIA GPU & nvidia-smi
echo "🔍 Checking GPU hardware..."
if ! command -v nvidia-smi &> /dev/null; then
    echo "❌ nvidia-smi could not be found. Please install NVIDIA drivers before running this script."
    exit 1
fi

GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n 1)
echo "✅ Detected GPU: $GPU_NAME"

# 3. Install dependencies (Python3, pip, Docker)
echo "📦 Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip curl docker.io > /dev/null

# 4. Install required Python packages
echo "🐍 Installing Python telemetry packages..."
pip3 install requests pynvml psutil --quiet

# 5. Create ComputeX directory structure
echo "📁 Setting up host directory..."
mkdir -p /opt/computex
cd /opt/computex

# 6. Download host_daemon.py
echo "📥 Fetching host daemon script..."
curl -sSL -o host_daemon.py https://raw.githubusercontent.com/israel1408/computex-host/main/host_daemon.py

# 7. Prompt for Discord Webhook / Node Config
echo ""
read -p "Enter your Node ID (e.g. node-01): " NODE_ID
read -p "Enter your Host Wallet Address (USDC): " WALLET_ADDR
read -p "Enter your Discord Webhook URL for #node-status: " WEBHOOK_URL

# Save configuration
cat <<EOF > /opt/computex/config.json
{
    "node_id": "$NODE_ID",
    "wallet": "$WALLET_ADDR",
    "webhook_url": "$WEBHOOK_URL"
}
EOF

# 8. Create Systemd Background Service
echo "⚙️ Configuring background system service..."
cat <<EOF > /etc/systemd/system/computex.service
[Unit]
Description=ComputeX Clearhouse Host Daemon
After=network.target docker.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/computex
ExecStart=/usr/bin/python3 /opt/computex/host_daemon.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Enable and start service
systemctl daemon-reload
systemctl enable computex.service
systemctl restart computex.service

echo ""
echo "=================================================="
echo "🚀 ComputeX Host Node is installed & running!"
echo "📊 Service status: sudo systemctl status computex"
echo "=================================================="
