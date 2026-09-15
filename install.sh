#!/usr/bin/env bash
set -e

echo "🚀 Starting ComputeX Host Setup..."

if [ -z "$HOST_API_KEY" ]; then
    echo "❌ Error: HOST_API_KEY environment variable is required."
    echo "Usage: HOST_API_KEY=\"your_key\" bash install.sh"
    exit 1
fi

echo "📦 Installing verification dependencies..."
pip install --quiet nvidia-ml-py torch requests

echo "🔎 Running hardware verification and benchmark..."
python3 verify_hardware.py

echo "🚀 Verification successful. Starting host daemon..."
python3 host_daemon.py
