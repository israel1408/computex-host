<div align="center">

# ⚡ ComputeX Clearhouse — Host Node Daemon

### *Monetize your idle RTX 3090 / 4090 GPUs on the Discord-native P2P AI Compute Marketplace.*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-brightgreen.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Docker Powered](https://img.shields.io/badge/Docker-Isolated-2496ED.svg?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)
[![Render](https://img.shields.io/badge/Render-Control_Plane-46E3B7.svg?style=for-the-badge&logo=render&logoColor=white)](https://render.com/)
[![Payouts](https://img.shields.io/badge/Payouts-USDC_(Base/Solana)-2775CA.svg?style=for-the-badge&logo=usdc&logoColor=white)](#)
[![Discord](https://img.shields.io/badge/Community-Discord-5865F2.svg?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/ymPSVsdKdU)
[![Status](https://img.shields.io/badge/Network-Active-success.svg?style=for-the-badge)](#)

<br />

> **Turn your idle gaming rig or mining server into an automated cash-flowing node.** Lease spare GPU compute power to AI engineers and creators right through Discord—earning **daily USDC payouts** at an **80/20 platform revenue split**.

<br />

<img src="https://www.serverbasket.net/ng/wp-content/uploads/2024/11/NVIDIA-GeForce-RTX-4090-24GB-GDDR6-Graphics-Card.jpg" width="750" alt="NVIDIA RTX 4090 Compute Node" style="border-radius: 10px;" />

</div>

---

## 📑 Table of Contents

- [📌 What is ComputeX Clearhouse?](#-what-is-computex-clearhouse)
- [💡 Why Host With ComputeX?](#-why-host-with-computex)
- [🏗️ System Architecture & Data Flow](#️-system-architecture--data-flow)
- [💻 Hardware & System Prerequisites](#-hardware--system-prerequisites)
- [🚀 Host Provider Quickstart](#-host-provider-quickstart)
- [🎮 Discord Slash Command Directory](#-discord-slash-command-directory)
- [💳 Billing Tiers & Automated Payouts](#-billing-tiers--automated-payouts)
- [🛠️ Control Plane Deployment Guide (Render)](#️-control-plane-deployment-guide-render)
- [🔌 REST API & Webhook Specifications](#-rest-api--webhook-specifications)
- [🧪 End-to-End Testing & Verification](#-end-to-end-testing--verification)
- [🔒 Security & Thermal Protection Architecture](#-security--thermal-protection-architecture)
- [📄 License & Community](#-license--community)

---

## 📌 What is ComputeX Clearhouse?

**ComputeX Clearhouse** connects creators, researchers, and AI engineers directly with GPU hardware owners in an open, decentralized peer-to-peer compute marketplace. 

By running the lightweight **ComputeX Host Daemon**, host nodes register their hardware capabilities and lease spare GPU compute power during idle hours. The entire rental lifecycle—from hardware discovery and payment processing to interactive console routing—is managed seamlessly inside **Discord**. Host providers earn **daily USDC payouts** settled on **Base or Solana** with an industry-leading **80/20 platform revenue split** (80% to host, 20% to protocol).

---

## 💡 Why Host With ComputeX?

| Feature | ComputeX Clearhouse | Traditional Cloud / Mining |
| :--- | :--- | :--- |
| **Payout Rates** | **$0.24 – $0.28 / hr** per RTX 4090 | $0.05 – $0.10 / hr (Crypto Mining) |
| **Payout Frequency** | **Daily automated USDC** (Base / Solana) | Monthly / High minimum thresholds |
| **Revenue Split** | **80% Host / 20% Platform** split | 50% – 70% Host payout margins |
| **Sandbox Security** | **Isolated Docker Containers** (No host disk access) | Full System Access Risk |
| **Thermal Safeguards** | **Auto-Throttling @ 80°C** | Risk of Hardware Overheating |
| **Port Forwarding** | **Zero open ports required** (Reverse `tmate` SSH Tunnels) | Complex router manipulation & public IPs |
| **Console Protocols** | **Dual Access**: Reverse SSH Console & Browser Web Terminal | Basic SSH only |
| **Metering & Billing** | **Per-second active deduction** via Whop webhooks | Hourly block charging |

---

## 🏗️ System Architecture & Data Flow

```text
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                              GPU HOST MACHINE                                │
 │                                                                              │
 │  ┌─────────────────────┐    ┌────────────────────┐    ┌───────────────────┐  │
 │  │ Hardware Detection  │ ──►│ Encrypted Tmate    │ ──►│ Authenticated     │  │
 │  │ (nvidia-smi VRAM)   │    │ Reverse Tunnel     │    │ Host Daemon Loop  │  │
 │  └─────────────────────┘    └────────────────────┘    └─────────┬─────────┘  │
 └─────────────────────────────────────────────────────────────────┼────────────┘
                                                                   │
                                         X-Host-API-Key Auth Header│
                                         (Heartbeats & Registration│
                                                                   ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                      CONTROL PLANE (`bot.py` on Render)                      │
 │                                                                              │
 │  ┌───────────────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
 │  │ HTTP Web Server (Port 8080)│  │ Billing & Heart-│  │ JSON State Store │  │
 │  │ Node / Whop Endpoint Auth │  │ beat Pruner Loop │  │ computex_db.json │  │
 │  └─────────────▲─────────────┘  └──────────────────┘  └──────────────────┘  │
 └────────────────┼────────────────────────────────────────────────▲────────────┘
                  │                                                │
    Payment Events│ Webhook                                        │ Slash Commands
 ┌────────────────┴───────────┐                        ┌───────────┴───────────┐
 │ Whop Checkout & Billing    │                        │  Discord User UI      │
 │ Automated Balance Top-up   │                        │ (/nodes, /rent_gpu)   │
 └────────────────────────────┘                        └───────────────────────┘
