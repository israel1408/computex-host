#!/bin/bash

# =======================================================
# ComputeX Clearhouse — Host Node Production Installer
# =======================================================

set -e

echo "⚡ Starting ComputeX Host Node Setup..."

if [ "$EUID" -ne 0 ]; then
  echo "❌ Please run with root privileges: sudo bash install.sh"
  exit 1
fi

# 1. Hardware Check
if ! command -v nvidia-smi &> /dev/null; then
    echo "❌ NVIDIA drivers missing! Run 'nvidia-smi' to verify drivers."
    exit 1
fi

# 2. Dependencies Setup
echo "📦 Installing system dependencies (Docker, tmate, Python)..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip curl docker.io tmate > /dev/null
pip3 install requests pynvml psutil --quiet

# 3. Create Operating Environment
mkdir -p /opt/computex
cd /opt/computex

# 4. Interactive Configuration Wizard
echo ""
echo "=========================================="
echo "      COMPUTEX HOST MODE SETUP            "
echo "=========================================="
read -p "Enter Node ID (e.g. node-01): " NODE_ID
read -p "Enter Host Wallet Address (USDC): " WALLET_ADDR
read -p "Enter Discord Webhook URL for #node-status: " WEBHOOK_URL

echo ""
echo "Choose Host Operating Mode:"
echo " 1) Always-On (24/7) — Automatically mines crypto when unrented (Zero Idle)"
echo " 2) Flexible / On-Demand — Standby state (0% GPU usage when unrented)"
read -p "Select Mode [1 or 2]: " MODE_CHOICE

if [ "$MODE_CHOICE" == "1" ]; then
    HOST_MODE="always_on"
    read -p "Enter Mining Wallet / Payout Address (KAS/RVN/XMR): " MINING_WALLET
else
    HOST_MODE="flexible"
    MINING_WALLET="N/A"
fi

# 5. Save Structured Configuration
cat <<EOF > /opt/computex/config.json
{
    "node_id": "$NODE_ID",
    "wallet": "$WALLET_ADDR",
    "webhook_url": "$WEBHOOK_URL",
    "host_mode": "$HOST_MODE",
    "mining_wallet": "$MINING_WALLET",
    "current_state": "IDLE"
}
EOF

# 6. Fetch Daemon Script
curl -sSL -o /opt/computex/host_daemon.py https://raw.githubusercontent.com/your-username/computex-host/main/host_daemon.py

# 7. Configure System Service
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

systemctl daemon-reload
systemctl enable computex.service
systemctl restart computex.service

echo ""
echo "=================================================="
echo "✅ ComputeX Host configured successfully as [$HOST_MODE]!"
echo "=================================================="
