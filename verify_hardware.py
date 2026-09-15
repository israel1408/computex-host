import os
import sys
import time
import requests
import torch
import pynvml

CENTRAL_API_URL = os.getenv("CENTRAL_API_URL", "https://api.computex.network/v1/hosts/verify")

def verify_and_benchmark():
    host_api_key = os.getenv("HOST_API_KEY")
    if not host_api_key:
        print("❌ Error: HOST_API_KEY environment variable is missing.")
        sys.exit(1)

    if not torch.cuda.is_available():
        print("❌ Error: No CUDA-compatible GPU detected on this host.")
        sys.exit(1)

    pynvml.nvmlInit()
    device_count = pynvml.nvmlDeviceGetCount()
    if device_count == 0:
        print("❌ Error: No NVIDIA GPU devices detected via NVML.")
        sys.exit(1)

    handle = pynvml.nvmlDeviceGetHandleByIndex(0)
    gpu_name = pynvml.nvmlDeviceGetName(handle)
    if isinstance(gpu_name, bytes):
        gpu_name = gpu_name.decode("utf-8")

    mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
    total_vram_gb = round(mem_info.total / (1024 ** 3), 2)
    driver_version = pynvml.nvmlSystemGetDriverVersion()
    if isinstance(driver_version, bytes):
        driver_version = driver_version.decode("utf-8")

    print(f"🔍 Hardware Detected: {gpu_name}")
    print(f"📊 Physical VRAM: {total_vram_gb} GB")
    print(f"⚙️ Driver Version: {driver_version}")
    print("⚡ Executing 10-iteration CUDA FP16 matrix multiplication benchmark...")

    device = torch.device("cuda:0")
    size = 8192

    try:
        a = torch.randn(size, size, device=device, dtype=torch.float16)
        b = torch.randn(size, size, device=device, dtype=torch.float16)

        # Warmup iteration
        _ = torch.matmul(a, b)
        torch.cuda.synchronize()

        start_time = time.time()
        for _ in range(10):
            _ = torch.matmul(a, b)
        torch.cuda.synchronize()
        elapsed_time = (time.time() - start_time) / 10.0

        flops = 2 * (size ** 3)
        tflops = round((flops / elapsed_time) / 1e12, 2)
        latency_ms = round(elapsed_time * 1000, 2)
        print(f"✅ Benchmark Complete: {tflops} TFLOPS (Avg Latency: {latency_ms} ms)")

    except Exception as err:
        print(f"❌ CUDA Benchmark Failed: {err}")
        sys.exit(1)
    finally:
        pynvml.nvmlShutdown()

    payload = {
        "host_api_key": host_api_key,
        "gpu_name": gpu_name,
        "vram_gb": total_vram_gb,
        "driver_version": driver_version,
        "measured_tflops": tflops,
        "benchmark_latency_ms": latency_ms
    }

    print("📡 Submitting hardware verification payload to Central API...")
    try:
        res = requests.post(CENTRAL_API_URL, json=payload, timeout=15)
        if res.status_code == 200:
            data = res.json()
            print(f"🎉 Host Verified Successfully! Assigned Tier: [{data.get('assigned_tier', 'Standard')}]")
        else:
            print(f"❌ Verification Rejected by Central API ({res.status_code}): {res.text}")
            sys.exit(1)
    except Exception as err:
        print(f"❌ Connection Error: {err}")
        sys.exit(1)

if __name__ == "__main__":
    verify_and_benchmark()
