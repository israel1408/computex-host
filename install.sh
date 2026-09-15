#!/usr/bin/env bash
set -e

echo "=================================================="
echo "🚀 ComputeX Host Node Automated Installer"
echo "=================================================="

if [ -z "$HOST_API_KEY" ]; then
    echo "❌ Error: HOST_API_KEY environment variable is required."
    echo "Usage: HOST_API_KEY=\"your_key\" bash install.sh"
    exit 1
fi

if [ "$EUID" -ne 0 ]; then
  echo "⚠️ Note: Run with root or sudo privileges to automatically configure the systemd background service."
fi

echo "📦 Step 1: Installing system and Python dependencies..."
apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv curl git build-essential

echo "📦 Step 2: Installing Python package requirements..."
pip3 install --quiet nvidia-ml-py torch requests psutil

echo "🔎 Step 3: Executing hardware verification & benchmarking..."
python3 verify_hardware.py

echo "⚙️ Step 4: Registering systemd service for host_daemon.py..."
SERVICE_FILE="/etc/systemd/system/computex-host.service"
WORKING_DIR=$(pwd)

cat <<EOF > $SERVICE_FILE
[Unit]
Description=ComputeX Host Node Telemetry & Daemon Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$WORKING_DIR
Environment="HOST_API_KEY=$HOST_API_KEY"
Environment="CENTRAL_API_URL=${CENTRAL_API_URL:-https://api.computex.network/v1/hosts/heartbeat}"
ExecStart=/usr/bin/python3 $WORKING_DIR/host_daemon.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable computex-host.service
systemctl restart computex-host.service

echo "=================================================="
echo "🎉 ComputeX Host Node successfully verified and running!"
echo "Check status anytime: systemctl status computex-host.service"
echo "=================================================="
