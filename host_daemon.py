import os
import sys
import time
import requests
import pynvml

CENTRAL_API_URL = os.getenv("CENTRAL_API_URL", "https://api.computex.network/v1/hosts/heartbeat")
HOST_API_KEY = os.getenv("HOST_API_KEY")
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "15"))

class HostDaemon:
    def __init__(self):
        if not HOST_API_KEY:
            print("❌ Error: HOST_API_KEY environment variable is missing.")
            sys.exit(1)
        
        pynvml.nvmlInit()
        self.handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        self.status = "IDLE"
        self.is_mining = False

    def get_telemetry(self):
        temp = pynvml.nvmlDeviceGetTemperature(self.handle, pynvml.NVML_TEMPERATURE_GPU)
        mem_info = pynvml.nvmlDeviceGetMemoryInfo(self.handle)
        used_vram_gb = round(mem_info.used / (1024 ** 3), 2)
        total_vram_gb = round(mem_info.total / (1024 ** 3), 2)
        utilization = pynvml.nvmlDeviceGetUtilizationRates(self.handle)

        return {
            "temperature_c": temp,
            "gpu_utilization_pct": utilization.gpu,
            "vram_used_gb": used_vram_gb,
            "vram_total_gb": total_vram_gb
        }

    def start_fallback_mining(self):
        if not self.is_mining:
            print("⛏️ Node is IDLE. Starting background fallback mining...")
            self.is_mining = True

    def stop_fallback_mining(self):
        if self.is_mining:
            print("🛑 Stopping background fallback mining for active rental workload...")
            self.is_mining = False

    def send_heartbeat(self, telemetry):
        payload = {
            "host_api_key": HOST_API_KEY,
            "status": self.status,
            "is_mining": self.is_mining,
            "telemetry": telemetry
        }
        try:
            res = requests.post(CENTRAL_API_URL, json=payload, timeout=5)
            if res.status_code == 200:
                data = res.json()
                remote_status = data.get("assigned_status")
                if remote_status == "RENTED" and self.status != "RENTED":
                    self.status = "RENTED"
                    self.stop_fallback_mining()
                elif remote_status == "IDLE" and self.status == "RENTED":
                    self.status = "IDLE"
                    self.start_fallback_mining()
        except Exception as e:
            print(f"⚠️ Heartbeat failed: {e}")

    def run(self):
        print("🚀 ComputeX Host Daemon started successfully.")
        self.start_fallback_mining()

        try:
            while True:
                telemetry = self.get_telemetry()
                
                # Thermal Protection Guardrail
                if telemetry["temperature_c"] > 85:
                    print(f"🔥 WARNING: High GPU Temperature ({telemetry['temperature_c']}°C)! Throttling node...")
                    self.status = "THERMAL_THROTTLED"
                    self.stop_fallback_mining()
                elif self.status == "THERMAL_THROTTLED" and telemetry["temperature_c"] <= 75:
                    print("❄️ Temperature normalized. Resuming standard operations...")
                    self.status = "IDLE"
                    self.start_fallback_mining()

                print(f"📊 [Telemetry] Temp: {telemetry['temperature_c']}°C | GPU Util: {telemetry['gpu_utilization_pct']}% | VRAM: {telemetry['vram_used_gb']}/{telemetry['vram_total_gb']} GB | Status: {self.status}")
                
                self.send_heartbeat(telemetry)
                time.sleep(HEARTBEAT_INTERVAL)

        except KeyboardInterrupt:
            print("🛑 Stopping ComputeX Host Daemon...")
            self.stop_fallback_mining()
        finally:
            pynvml.nvmlShutdown()

if __name__ == "__main__":
    daemon = HostDaemon()
    daemon.run()
