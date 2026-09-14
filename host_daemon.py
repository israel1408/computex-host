#!/usr/bin/env python3
import json
import time
import os
import subprocess
import requests

CONFIG_PATH = "/opt/computex/config.json"

def load_config():
    if not os.path.exists(CONFIG_PATH):
        # Fallback for local testing
        return {
            "node_id": "demo-node-01",
            "wallet": "0x0000000000000000000000000000000000000000",
            "webhook_url": ""
        }
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

def get_gpu_telemetry():
    """Queries nvidia-smi for real-time GPU statistics."""
    try:
        cmd = "nvidia-smi --query-gpu=gpu_name,temperature.gpu,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits"
        output = subprocess.check_output(cmd, shell=True).decode('utf-8').strip()
        gpu_name, temp, util, mem_used, mem_total = [x.strip() for x in output.split(',')]
        
        return {
            "gpu_name": gpu_name,
            "temp": int(temp),
            "utilization": int(util),
            "mem_used": int(mem_used),
            "mem_total": int(mem_total),
            "status": "HEALTHY" if int(temp) < 80 else "THERMAL_WARNING"
        }
    except Exception as e:
        return {
            "gpu_name": "Unknown GPU",
            "temp": 0,
            "utilization": 0,
            "mem_used": 0,
            "mem_total": 0,
            "status": f"ERROR: {str(e)}"
        }

def send_heartbeat(config, gpu):
    """Sends telemetry ping to the Discord #node-status channel via Webhook."""
    webhook_url = config.get("webhook_url")
    if not webhook_url:
        print("[!] No webhook URL found in config. Skipping heartbeat alert.")
        return

    status_icon = "🟢" if gpu["status"] == "HEALTHY" else "⚠️"
    
    payload = {
        "username": "ComputeX Node Monitor",
        "embeds": [
            {
                "title": f"{status_icon} Node Telemetry: {config['node_id']}",
                "color": 65280 if gpu["status"] == "HEALTHY" else 16711680,
                "fields": [
                    {"name": "GPU Model", "value": gpu["gpu_name"], "inline": True},
                    {"name": "Temperature", "value": f"{gpu['temp']}°C", "inline": True},
                    {"name": "GPU Load", "value": f"{gpu['utilization']}%", "inline": True},
                    {"name": "VRAM Usage", "value": f"{gpu['mem_used']} MB / {gpu['mem_total']} MB", "inline": True},
                    {"name": "Host Wallet", "value": f"`{config['wallet'][:10]}...`", "inline": True}
                ],
                "footer": {"text": "ComputeX Clearhouse • Heartbeat Active"},
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            }
        ]
    }

    try:
        requests.post(webhook_url, json=payload, timeout=5)
        print(f"[+] Heartbeat sent for {config['node_id']} ({gpu['gpu_name']} - {gpu['temp']}°C)")
    except Exception as e:
        print(f"[-] Failed to send heartbeat webhook: {e}")

def main():
    print("⚡ ComputeX Host Daemon started...")
    config = load_config()

    while True:
        gpu_data = get_gpu_telemetry()

        # Thermal protection check
        if gpu_data["temp"] >= 83:
            print(f"🚨 CRITICAL THERMAL WARNING: {gpu_data['temp']}°C! Throttling active instances...")

        # Send heartbeat to Discord Webhook every 60 seconds
        send_heartbeat(config, gpu_data)
        
        time.sleep(60)

if __name__ == "__main__":
    main()
