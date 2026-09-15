import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import discord
from discord import app_commands
from discord.ext import commands, tasks

# ---------------------------------------------------------
# Configuration & Environment Setup
# ---------------------------------------------------------
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
HOST_API_KEY = os.getenv("HOST_API_KEY")
PORT = int(os.getenv("PORT", 8080))

if not DISCORD_BOT_TOKEN:
  raise ValueError(
      "❌ ERROR: DISCORD_BOT_TOKEN environment variable is missing."
  )
if not HOST_API_KEY:
  raise ValueError("❌ ERROR: HOST_API_KEY environment variable is missing.")

# Persistent Storage Path (Uses Render Persistent Disk at /data if present)
DATA_DIR = "/data" if os.path.exists("/data") else "."
DATABASE_FILE = os.path.join(DATA_DIR, "computex_db.json")


# ---------------------------------------------------------
# Database Helper Functions
# ---------------------------------------------------------
def load_db():
  if not os.path.exists(DATABASE_FILE):
    default_db = {"users": {}, "nodes": {}, "rentals": {}}
    save_db(default_db)
    return default_db
  try:
    with open(DATABASE_FILE, "r") as f:
      return json.load(f)
  except Exception as e:
    print(f"⚠️ Error reading database file: {e}")
    return {"users": {}, "nodes": {}, "rentals": {}}


def save_db(data):
  try:
    with open(DATABASE_FILE, "w") as f:
      json.dump(data, f, indent=2)
  except Exception as e:
    print(f"❌ Error saving database file: {e}")


# ---------------------------------------------------------
# Control Plane HTTP Web Server & Webhook Handler
# ---------------------------------------------------------
class ControlPlaneHandler(BaseHTTPRequestHandler):

  def log_message(self, format, *args):
    return  # Suppress default HTTP log spam

  def do_GET(self):
    if self.path == "/":
      self.send_response(200)
      self.send_header("Content-Type", "text/plain")
      self.end_headers()
      self.wfile.write(b"ComputeX Bot & API Operational")
    else:
      self.send_response(404)
      self.end_headers()

  def do_POST(self):
    db = load_db()
    auth_header = self.headers.get("X-Host-API-Key")

    # Route 1: Register Node
    if self.path == "/register-node":
      if auth_header != HOST_API_KEY:
        self.send_response(401)
        self.end_headers()
        self.wfile.write(b"Unauthorized")
        return

      content_length = int(self.headers.get("Content-Length", 0))
      body = self.rfile.read(content_length)
      data = json.loads(body.decode("utf-8"))

      node_id = data.get("node_id")
      db["nodes"][node_id] = {
          "node_id": node_id,
          "gpu_name": data.get("gpu_name", "NVIDIA GPU"),
          "vram": data.get("vram", "Unknown"),
          "hourly_rate": float(data.get("hourly_rate", 0.30)),
          "ssh_cmd": data.get("ssh_cmd", ""),
          "web_cmd": data.get("web_cmd", ""),
          "status": "ONLINE",
          "last_seen": time.time(),
      }
      save_db(db)
      print(f"🟢 Node Registered: {node_id}")

      self.send_response(200)
      self.end_headers()
      self.wfile.write(b"Registered")

    # Route 2: Node Heartbeat
    elif self.path == "/heartbeat":
      if auth_header != HOST_API_KEY:
        self.send_response(401)
        self.end_headers()
        self.wfile.write(b"Unauthorized")
        return

      content_length = int(self.headers.get("Content-Length", 0))
      body = self.rfile.read(content_length)
      data = json.loads(body.decode("utf-8"))

      node_id = data.get("node_id")
      if node_id in db["nodes"]:
        db["nodes"][node_id]["last_seen"] = time.time()
        if db["nodes"][node_id]["status"] == "OFFLINE":
          db["nodes"][node_id]["status"] = "ONLINE"
        save_db(db)

      self.send_response(200)
      self.end_headers()
      self.wfile.write(b"Heartbeat ACK")

    # Route 3: Whop Payment Webhook
    elif self.path == "/whop-webhook":
      content_length = int(self.headers.get("Content-Length", 0))
      body = self.rfile.read(content_length)
      payload = json.loads(body.decode("utf-8"))

      if payload.get("action") == "payment.succeeded":
        data = payload.get("data", {})
        custom_fields = data.get("custom_fields", {})
        user_data = data.get("user", {}) or payload.get("user", {})

        # Comprehensive fallback pipeline for Discord ID resolution
        discord_id = (
            custom_fields.get("discord_user_id")
            or custom_fields.get("Discord User ID")
            or user_data.get("discord_id")
            or user_data.get("social_accounts", {})
            .get("discord", {})
            .get("id")
            or data.get("metadata", {}).get("discord_user_id")
        )

        raw_amount = float(data.get("final_amount", data.get("amount", 0)))
        if raw_amount > 500:
          raw_amount = raw_amount / 100.0
        amount_paid = round(raw_amount, 2)

        tier_name = (
            "Starter"
            if amount_paid == 14.99
            else ("Pro" if amount_paid == 39.99 else "Studio")
        )
        credit_amount = amount_paid

        if discord_id:
          discord_id = str(discord_id).strip()
          if discord_id not in db["users"]:
            db["users"][discord_id] = {"balance": 0.00, "tier": tier_name}
          db["users"][discord_id]["balance"] += credit_amount
          db["users"][discord_id]["tier"] = tier_name
          save_db(db)
          print(
              f"✅ Credited ${credit_amount} USD to Discord User {discord_id}"
          )
        else:
          print("⚠️ Webhook received but no Discord ID could be extracted.")

      self.send_response(200)
      self.end_headers()
      self.wfile.write(b"OK")

    else:
      self.send_response(404)
      self.end_headers()


def run_http_server():
  server = HTTPServer(("0.0.0.0", PORT), ControlPlaneHandler)
  print(f"🌐 HTTP Control Plane Server running on port {PORT}")
  server.serve_forever()


# ---------------------------------------------------------
# Discord Bot & Slash Commands
# ---------------------------------------------------------
intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
  print(f"⚡ ComputeX Clearhouse Bot Active: {bot.user}")
  try:
    synced = await bot.tree.sync()
    print(f"✅ Synced {len(synced)} Slash Commands")
  except Exception as e:
    print(f"❌ Error syncing commands: {e}")
  prune_dead_nodes.start()


@tasks.loop(seconds=30)
async def prune_dead_nodes():
  db = load_db()
  now = time.time()
  updated = False
  for node_id, node in db["nodes"].items():
    if node["status"] == "ONLINE" and (now - node.get("last_seen", 0)) > 120:
      node["status"] = "OFFLINE"
      updated = True
      print(f"🔴 Node timed out and marked OFFLINE: {node_id}")
  if updated:
    save_db(db)


@bot.tree.command(
    name="balance", description="Check your compute credit balance and tier."
)
async def balance(interaction: discord.Interaction):
  db = load_db()
  user_id = str(interaction.user.id)
  user_info = db["users"].get(user_id, {"balance": 0.00, "tier": "Free"})

  embed = discord.Embed(title="💳 Account Balance", color=0x46E3B7)
  embed.add_field(
      name="Available Balance",
      value=f"`${user_info['balance']:.2f} USD`",
      inline=True,
  )
  embed.add_field(
      name="Current Tier", value=f"`{user_info['tier']}`", inline=True
  )
  await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="nodes", description="List active GPU compute nodes on the network."
)
async def nodes(interaction: discord.Interaction):
  db = load_db()
  embed = discord.Embed(
      title="⚡ ComputeX Clearhouse Network Nodes", color=0x5865F2
  )

  if not db["nodes"]:
    embed.description = "No host nodes registered on the network."
  else:
    for node_id, node in db["nodes"].items():
      status_emoji = (
          "🟢"
          if node["status"] == "ONLINE"
          else ("🟡" if node["status"] == "RENTED" else "🔴")
      )
      embed.add_field(
          name=f"{status_emoji} {node['node_id']} ({node['gpu_name']})",
          value=(
              f"**VRAM:** {node['vram']}\n**Rate:**"
              f" `${node['hourly_rate']:.2f}/hr`\n**Status:** {node['status']}"
          ),
          inline=False,
      )
  await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="rent_gpu", description="Rent an available GPU compute node."
)
async def rent_gpu(interaction: discord.Interaction):
  db = load_db()
  user_id = str(interaction.user.id)
  user_info = db["users"].get(user_id, {"balance": 0.00, "tier": "Free"})

  if user_info["balance"] < 0.30:
    embed = discord.Embed(
        title="❌ Insufficient Balance",
        description=(
            "Your balance is below $0.30. Please top up your account on Whop"
            " to rent a GPU."
        ),
        color=0xFF4B4B,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)
    return

  if user_id in db["rentals"]:
    await interaction.response.send_message(
        "❌ You already have an active GPU rental. Run `/stop_rental` first.",
        ephemeral=True,
    )
    return

  available_node = None
  for n_id, node in db["nodes"].items():
    if node["status"] == "ONLINE":
      available_node = node
      break

  if not available_node:
    await interaction.response.send_message(
        "❌ No GPU nodes are currently available for rent.", ephemeral=True
    )
    return

  node_id = available_node["node_id"]
  db["nodes"][node_id]["status"] = "RENTED"
  db["rentals"][user_id] = {
      "node_id": node_id,
      "start_time": time.time(),
      "hourly_rate": available_node["hourly_rate"],
  }
  save_db(db)

  embed = discord.Embed(title="🚀 GPU Rental Session Active", color=0x46E3B7)
  embed.add_field(name="Assigned Node", value=f"`{node_id}`", inline=False)
  embed.add_field(
      name="Web Terminal",
      value=f"[Open Browser Console]({available_node['web_cmd']})",
      inline=False,
  )
  embed.add_field(
      name="SSH Command", value=f"`{available_node['ssh_cmd']}`", inline=False
  )
  embed.set_footer(
      text="Run /stop_rental when finished to stop per-second billing."
  )

  await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="stop_rental",
    description="Terminate your active GPU rental and settle balance.",
)
async def stop_rental(interaction: discord.Interaction):
  db = load_db()
  user_id = str(interaction.user.id)

  if user_id not in db["rentals"]:
    await interaction.response.send_message(
        "❌ You do not have an active rental session.", ephemeral=True
    )
    return

  rental = db["rentals"][user_id]
  node_id = rental["node_id"]
  elapsed_seconds = max(1, int(time.time() - rental["start_time"]))
  cost = (elapsed_seconds / 3600.0) * rental["hourly_rate"]

  if user_id in db["users"]:
    db["users"][user_id]["balance"] = max(
        0.00, db["users"][user_id]["balance"] - cost
    )

  if node_id in db["nodes"]:
    db["nodes"][node_id]["status"] = "ONLINE"

  del db["rentals"][user_id]
  save_db(db)

  embed = discord.Embed(title="🛑 Rental Terminated", color=0xFF9900)
  embed.add_field(
      name="Duration", value=f"`{elapsed_seconds}` seconds", inline=True
  )
  embed.add_field(name="Total Cost", value=f"`${cost:.4f} USD`", inline=True)
  embed.add_field(
      name="Remaining Balance",
      value=f"`${db['users'].get(user_id, {}).get('balance', 0.0):.2f} USD`",
      inline=True,
  )

  await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------
# Main Execution Entry Point
# ---------------------------------------------------------
if __name__ == "__main__":
  http_thread = threading.Thread(target=run_http_server, daemon=True)
  http_thread.start()
  bot.run(DISCORD_BOT_TOKEN)
