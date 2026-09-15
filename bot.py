import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import discord
from discord import app_commands
from discord.ext import commands, tasks

# =======================================================
# CONFIGURATION & DATABASE SETUP
# =======================================================
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
HOST_API_KEY = os.getenv("HOST_API_KEY", "computex-prod-secret-2026")
DATABASE_FILE = "computex_db.json"


def load_db():
  if not os.path.exists(DATABASE_FILE):
    return {"users": {}, "nodes": {}, "active_sessions": {}}
  try:
    with open(DATABASE_FILE, "r") as f:
      return json.load(f)
  except Exception:
    return {"users": {}, "nodes": {}, "active_sessions": {}}


def save_db(db_data):
  with open(DATABASE_FILE, "w") as f:
    json.dump(db_data, f, indent=4)


db = load_db()


# =======================================================
# RENDER SERVER (HEALTH CHECK, WHOP & SECURE HOST API)
# =======================================================
class HealthCheckHandler(BaseHTTPRequestHandler):

  def do_GET(self):
    self.send_response(200)
    self.end_headers()
    self.wfile.write(b"ComputeX Bot & API Operational")

  def do_POST(self):
    clean_path = self.path.split("?")[0].rstrip("/")
    content_length = int(self.headers.get("Content-Length", 0))
    post_data = self.rfile.read(content_length) if content_length > 0 else b"{}"

    try:
      payload = json.loads(post_data.decode("utf-8"))

      # Whop Billing Webhook (Public)
      if clean_path == "/whop-webhook":
        if payload.get("action") == "payment.succeeded":
          data = payload.get("data", {})
          custom_fields = data.get("custom_fields", {})
          discord_id = custom_fields.get("discord_user_id")

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
            discord_id = str(discord_id)
            if discord_id not in db["users"]:
              db["users"][discord_id] = {"balance": 0.00, "tier": tier_name}
            db["users"][discord_id]["balance"] += credit_amount
            db["users"][discord_id]["tier"] = tier_name
            save_db(db)

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

      # Authenticated Host API Endpoints
      elif clean_path in ["/register-node", "/heartbeat"]:
        client_key = self.headers.get("X-Host-API-Key")
        if client_key != HOST_API_KEY:
          self.send_response(401)
          self.end_headers()
          self.wfile.write(b"Unauthorized: Invalid Key")
          return

        node_id = payload.get("node_id")

        if clean_path == "/register-node":
          if node_id:
            db["nodes"][node_id] = {
                "gpu": payload.get("gpu_name", "NVIDIA GPU"),
                "vram": payload.get("vram", "Unknown"),
                "rate": payload.get("hourly_rate", 0.30),
                "ssh_cmd": payload.get(
                    "ssh_cmd", "ssh disabled@tmate.io"
                ),  # Real tmate SSH
                "web_cmd": payload.get(
                    "web_cmd", "https://tmate.io"
                ),  # Real tmate Web URL
                "status": "ONLINE",
                "last_ping": time.time(),
            }
            save_db(db)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Registered")

        elif clean_path == "/heartbeat":
          if node_id and node_id in db["nodes"]:
            db["nodes"][node_id]["last_ping"] = time.time()
            if db["nodes"][node_id]["status"] == "OFFLINE":
              db["nodes"][node_id]["status"] = "ONLINE"
            save_db(db)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Heartbeat ACK")

      else:
        self.send_response(404)
        self.end_headers()

    except Exception as e:
      self.send_response(400)
      self.end_headers()


def run_health_check():
  port = int(os.getenv("PORT", 8080))
  server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
  server.serve_forever()


threading.Thread(target=run_health_check, daemon=True).start()

# =======================================================
# DISCORD BOT INITIALIZATION & COMMANDS
# =======================================================
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
  print(f"⚡ ComputeX Clearhouse Bot Active: {bot.user}")
  await bot.tree.sync()
  if not billing_and_heartbeat_loop.is_running():
    billing_and_heartbeat_loop.start()


@bot.tree.command(name="balance", description="View your credit balance.")
async def balance(interaction: discord.Interaction):
  user_id = str(interaction.user.id)
  user_data = db["users"].get(user_id, {"balance": 0.00, "tier": "Free"})

  embed = discord.Embed(
      title="💳 Account Balance", color=discord.Color.blue()
  )
  embed.add_field(
      name="Available Credits",
      value=f"**${user_data['balance']:.2f} USD**",
      inline=True,
  )
  embed.add_field(
      name="Subscription Tier", value=user_data["tier"], inline=True
  )
  await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="nodes", description="List all active GPU host nodes."
)
async def nodes(interaction: discord.Interaction):
  embed = discord.Embed(
      title="🖥️ Network GPU Nodes", color=discord.Color.green()
  )
  online_nodes = {
      k: v for k, v in db["nodes"].items() if v.get("status") != "OFFLINE"
  }

  if not online_nodes:
    embed.description = "No active GPU host nodes."
  else:
    for node_id, data in online_nodes.items():
      status_icon = "🟢" if data.get("status") == "ONLINE" else "🟡"
      embed.add_field(
          name=f"{status_icon} Node: {node_id}",
          value=(
              f"**GPU:** {data['gpu']}\n**VRAM:** {data['vram']}\n**Rate:**"
              f" ${data['rate']}/hr\n**Status:** {data['status']}"
          ),
          inline=True,
      )

  await interaction.response.send_message(embed=embed)


class TemplateSelect(discord.ui.Select):

  def __init__(self, node_id):
    self.node_id = node_id
    options = [
        discord.SelectOption(
            label="💻 Reverse SSH Console Access", value="ssh"
        ),
        discord.SelectOption(
            label="🌐 Web Terminal Session", value="web"
        ),
    ]
    super().__init__(
        placeholder="Select access type...", options=options
    )

  async def callback(self, interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    choice = self.values[0]

    session_id = f"session_{int(time.time())}"
    db["active_sessions"][session_id] = {
        "user_id": user_id,
        "node_id": self.node_id,
        "start_time": time.time(),
        "rate_per_sec": db["nodes"][self.node_id]["rate"] / 3600.0,
    }
    db["nodes"][self.node_id]["status"] = "RENTED"
    save_db(db)

    node_data = db["nodes"][self.node_id]
    embed = discord.Embed(
        title="🚀 GPU Rental Session Started", color=discord.Color.gold()
    )
    embed.add_field(name="Node ID", value=self.node_id, inline=True)

    if choice == "ssh":
      embed.add_field(
          name="🔑 Terminal Connection String",
          value=f"`{node_data.get('ssh_cmd')}`",
          inline=False,
      )
    else:
      embed.add_field(
          name="🌐 Web Console Link",
          value=f"[Open Browser Terminal]({node_data.get('web_cmd')})",
          inline=False,
      )

    embed.set_footer(text="Run /stop_rental to finish session.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


class TemplateView(discord.ui.View):

  def __init__(self, node_id):
    super().__init__()
    self.add_item(TemplateSelect(node_id))


@bot.tree.command(name="rent_gpu", description="Rent an available GPU node.")
async def rent_gpu(interaction: discord.Interaction):
  user_id = str(interaction.user.id)
  user_data = db["users"].get(user_id, {"balance": 0.00})

  if user_data.get("balance", 0.00) < 0.30:
    await interaction.response.send_message(
        "❌ Minimum balance of $0.30 required to start rental.", ephemeral=True
    )
    return

  target_node = next(
      (
          n_id
          for n_id, n_data in db["nodes"].items()
          if n_data.get("status") == "ONLINE"
      ),
      None,
  )
  if not target_node:
    await interaction.response.send_message(
        "🔴 No online nodes available.", ephemeral=True
    )
    return

  await interaction.response.send_message(
      "⚡ **Choose access protocol:**",
      view=TemplateView(target_node),
      ephemeral=True,
  )


@bot.tree.command(name="stop_rental", description="Terminate your rental session.")
async def stop_rental(interaction: discord.Interaction):
  user_id = str(interaction.user.id)
  session_id, session_data = next(
      (
          (s_id, s_data)
          for s_id, s_data in db["active_sessions"].items()
          if s_data.get("user_id") == user_id
      ),
      (None, None),
  )

  if not session_id:
    await interaction.response.send_message(
        "❌ No active rental session found.", ephemeral=True
    )
    return

  duration = time.time() - session_data["start_time"]
  cost = duration * session_data["rate_per_sec"]

  db["users"][user_id]["balance"] -= cost
  db["nodes"][session_data["node_id"]]["status"] = "ONLINE"
  del db["active_sessions"][session_id]
  save_db(db)

  await interaction.response.send_message(
      f"🛑 Rental stopped. Duration: {int(duration // 60)} min. Total cost:"
      f" ${cost:.4f} USD.",
      ephemeral=True,
  )


# =======================================================
# METERING & AUTOMATED HEARTBEAT PRUNER
# =======================================================
@tasks.loop(seconds=60)
async def billing_and_heartbeat_loop():
  now = time.time()

  # Deduct active rental balances
  for s_id, s_data in list(db["active_sessions"].items()):
    u_id = s_data["user_id"]
    minute_cost = s_data["rate_per_sec"] * 60.0
    if u_id in db["users"]:
      db["users"][u_id]["balance"] -= minute_cost
      if db["users"][u_id]["balance"] <= 0:
        db["nodes"][s_data["node_id"]]["status"] = "ONLINE"
        del db["active_sessions"][s_id]

  # Prune dead nodes (> 120s missing heartbeat)
  for n_id, n_data in db["nodes"].items():
    if (
        now - n_data.get("last_ping", 0) > 120
        and n_data.get("status") != "RENTED"
    ):
      n_data["status"] = "OFFLINE"

  save_db(db)


if __name__ == "__main__":
  bot.run(TOKEN)
