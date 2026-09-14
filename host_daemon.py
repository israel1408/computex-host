#!/usr/bin/env python3
import json
import time
import os
import subprocess
import requests

CONFIG_PATH = "/opt/computex/config.json"
MINER_CONTAINER = "computex-fallback-miner"
RENTER_CONTAINER = "computex-renter-workload"

class HostEngine:
    def __init__(self):
        self.config = self.load_config()
        self.state = "INITIALIZING"
        self.tunnel_url = None

    def load_config(self):
        if not os.path.exists(CONFIG_PATH):
            return {
                "node_id": "demo-node",
                "wallet": "0x000",
                "webhook_url": "",
                "host_mode": "always_on",
                "mining_wallet": "demo"
            }
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)

    def run_cmd(self, cmd):
        try:
            return subprocess.check_output(cmd, shell=True).decode('utf-8').strip()
        except Exception:
            return ""

    def get_gpu_telemetry(self):
        try:
            cmd = "nvidia-smi --query-gpu=gpu_name,temperature.gpu,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits"
            output = self.run_cmd(cmd)
            gpu_name, temp, util, mem_used, mem_total = [x.strip() for x in output.split(',')]
            return {
                "name": gpu_name,
                "temp": int(temp),
                "util": int(util),
                "mem_used": int(mem_used),
                "mem_total": int(mem_total)
            }
        except Exception:
            return {"name": "NVIDIA GPU", "temp": 0, "util": 0, "mem_used": 0, "mem_total": 0}

    # --- WORKLOAD & MINING CONTROLLERS ---

    def start_fallback_miner(self):
        """Starts background mining container when host is idle."""
        if self.config.get("host_mode") != "always_on":
            return
        
        # Check if already running
        running = self.run_cmd(f"docker ps -q -f name={MINER_CONTAINER}")
        if not running:
            print("[+] Starting Fallback Crypto Miner (Zero-Idle Mode)...")
            # Example using lightweight background miner container
            cmd = f"docker run -d --name {MINER_CONTAINER} --gpus all rigelminer/rigel:latest -a kawpow -o stratum+tcp://kp.unmineable.com:3333 -u RVN:{self.config['mining_wallet']}.{self.config['node_id']} --quiet"
            self.run_cmd(cmd)

    def stop_fallback_miner(self):
        """Stops background mining container to free up GPU for renter."""
        running = self.run_cmd(f"docker ps -q -f name={MINER_CONTAINER}")
        if running:
            print("[!] Pausing Fallback Miner for incoming rental job...")
            self.run_cmd(f"docker stop {MINER_CONTAINER} && docker rm {MINER_CONTAINER}")

    def spawn_reverse_tunnel(self):
        """Creates a secure reverse SSH tunnel using tmate (no open router ports)."""
        print("[+] Creating Reverse SSH Tunnel...")
        self.run_cmd("tmate -S /tmp/tmate.sock new-session -d")
        time.sleep(2)
        ssh_cmd = self.run_cmd("tmate -S /tmp/tmate.sock display -p '#{tmate_ssh}'")
        self.tunnel_url = ssh_cmd
        return ssh_cmd

    def kill_rent_session(self):
        """Cleanly stops renter workload and closes SSH tunnel."""
        print("[!] Terminating Renter Session...")
        self.run_cmd(f"docker stop {RENTER_CONTAINER} && docker rm {RENTER_CONTAINER}")
        self.run_cmd("tmate -S /tmp/tmate.sock kill-session")
        self.tunnel_url = None
        self.state = "IDLE"

    # --- TELEMETRY & DISCORD DISPATCH ---

    def send_telemetry_ping(self, gpu):
        webhook = self.config.get("webhook_url")
        if not webhook:
            return

        status_color = 65280 if gpu["temp"] < 80 else 16711680
        mode_label = "24/7 Always-On" if self.config.get("host_mode") == "always_on" else "Flexible / On-Demand"

        payload = {
            "username": "ComputeX Node Telemetry",
            "embeds": [{
                "title": f"Node Status: {self.config['node_id']} ({self.state})",
                "color": status_color,
                "fields": [
                    {"name": "Operating Mode", "value": mode_label, "inline": True},
                    {"name": "Current State", "value": f"`{self.state}`", "inline": True},
                    {"name": "GPU Temp", "value": f"{gpu['temp']}°C", "inline": True},
                    {"name": "GPU Load", "value": f"{gpu['util']}%", "inline": True},
                    {"name": "VRAM Usage", "value": f"{gpu['mem_used']} / {gpu['mem_total']} MB", "inline": True},
                    {"name": "Reverse SSH String", "value": f"`{self.tunnel_url or 'None (Not Rented)'}`", "inline": False}
                ],
                "footer": {"text": "ComputeX Clearhouse Daemon v2.0"},
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            }]
        }
        try:
            requests.post(webhook, json=payload, timeout=5)
        except Exception as e:
            print(f"[-] Webhook error: {e}")

    # --- MAIN STATE ENGINE LOOP ---

    def run(self):
        print(f"⚡ ComputeX Host Daemon Initialized [{self.config['node_id']}]")
        self.state = "IDLE"

        while True:
            gpu = self.get_gpu_telemetry()

            # 1. Thermal Emergency Safeguard
            if gpu["temp"] >= 83:
                print(f"🚨 THERMAL CRITICAL ({gpu['temp']}°C)! Killing high-load tasks...")
                self.kill_rent_session()
                self.stop_fallback_miner()
                self.state = "THERMAL_COOLDOWN"
                time.sleep(30)
                continue

            # 2. State Controller
            if self.state == "IDLE":
                if self.config.get("host_mode") == "always_on":
                    self.start_fallback_miner()
            
            # 3. Telemetry Broadcast
            self.send_telemetry_ping(gpu)
            time.sleep(60)

if __name__ == "__main__":
    engine = HostEngine()
    engine.run()
