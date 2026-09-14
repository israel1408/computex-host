import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import discord
from discord import app_commands
from discord.ext import commands, tasks

# =======================================================
# 1. CONFIGURATION & DATABASE SETUP
# =======================================================
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DATABASE_FILE = "computex_db.json"


def load_db():
  if not os.path.exists(DATABASE_FILE):
    return {"users": {}, "nodes": {}, "active_sessions": {}}
  try:
    with open(DATABASE_FILE, "r") as f:
      return json.load(f)
  except Exception as e:
    print(f"⚠️ Error loading database file: {e}. Starting fresh.")
    return {"users": {}, "nodes": {}, "active_sessions": {}}


def save_db(db_data):
  with open(DATABASE_FILE, "w") as f:
    json.dump(db_data, f, indent=4)


db = load_db()

# =======================================================
# 2. RENDER HEALTH CHECK & WEBHOOK SERVER
# =======================================================
class HealthCheckHandler(BaseHTTPRequestHandler):

  def do_GET(self):
    self.send_response(200)
    self.end_headers()
    self.wfile.write(b"ComputeX Bot & Webhook Server Online!")

  def do_POST(self):
    clean_path = self.path.split("?")[0].rstrip("/")
    content_length = int(self.headers.get("Content-Length", 0))
    post_data = self.rfile.read(content_length) if content_length > 0 else b"{}"

    try:
      payload = json.loads(post_data.decode("utf-8"))

      # ---------------------------------------------------
      # A. WHOP PAYMENT WEBHOOK
      # ---------------------------------------------------
      if clean_path == "/whop-webhook":
        event_type = payload.get("action")
        if event_type == "payment.succeeded":
          data = payload.get("data", {})
          custom_fields = data.get("custom_fields", {})
          discord_id = custom_fields.get("discord_user_id")

          raw_amount = float(data.get("final_amount", data.get("amount", 0)))
          if raw_amount > 500:
            raw_amount = raw_amount / 100.0
          amount_paid = round(raw_amount, 2)

          tier_name = "Paid"
          if amount_paid == 14.99:
            credit_amount = 15.00
            tier_name = "Starter"
          elif amount_paid == 39.99:
            credit_amount = 40.00
            tier_name = "Pro"
          elif amount_paid == 149.99:
            credit_amount = 150.00
            tier_name = "Studio"
          else:
            credit_amount = amount_paid

          if discord_id:
            discord_id = str(discord_id)
            if discord_id not in db["users"]:
              db["users"][discord_id] = {"balance": 0.00, "tier": tier_name}

            db["users"][discord_id]["balance"] += credit_amount
            db["users"][discord_id]["tier"] = tier_name
            save_db(db)
            print(
                f"💰 Added ${credit_amount} ({tier_name}) credits to User ID:"
                f" {discord_id}"
            )

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Webhook Processed Successfully")

      # ---------------------------------------------------
      # B. GPU HOST NODE REGISTRATION
      # ---------------------------------------------------
      elif clean_path == "/register-node":
        node_id = payload.get("node_id")
        gpu_name = payload.get("gpu_name")
        vram = payload.get("vram")
        hourly_rate = payload.get("hourly_rate", 0.30)
        status = payload.get("status", "ONLINE")

        if node_id:
          db["nodes"][node_id] = {
              "gpu": gpu_name,
              "vram": vram,
              "rate": hourly_rate,
              "status": status,
              "mode": "Docker/SSH",
              "last_ping": time.time(),
          }
          save_db(db)
          print(
              f"🖥️ Registered new GPU Host Node: {node_id} ({gpu_name}, {vram})"
          )

          self.send_response(200)
          self.end_headers()
          self.wfile.write(b"Node Registered Successfully")
        else:
          self.send_response(400)
          self.end_headers()
          self.wfile.write(b"Missing node_id")

      else:
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"Not Found")

    except Exception as e:
      print(f"❌ Server Processing Error: {e}")
      self.send_response(400)
      self.end_headers()
      self.wfile.write(b"Invalid Payload")


def run_health_check():
  port = int(os.getenv("PORT", 8080))
  server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
  server.serve_forever()


threading.Thread(target=run_health_check, daemon=True).start()

# =======================================================
# 3. BOT INITIALIZATION
# =======================================================
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
  print(f"⚡ ComputeX Clearhouse Bot Online: {bot.user}")
  try:
    synced = await bot.tree.sync()
    print(f"✅ Synced {len(synced)} Slash Commands.")
  except Exception as e:
    print(f"❌ Slash command sync error: {e}")

  if not meter_active_sessions.is_running():
    meter_active_sessions.start()


# =======================================================
# 4. SLASH COMMANDS & UI COMPONENTS
# =======================================================
@bot.tree.command(
    name="balance", description="Check your remaining ComputeX credit balance."
)
async def balance(interaction: discord.Interaction):
  user_id = str(interaction.user.id)
  user_data = db["users"].get(user_id, {"balance": 0.00, "tier": "Free"})

  embed = discord.Embed(
      title="💳 ComputeX Account Balance", color=discord.Color.blue()
  )
  embed.add_field(
      name="Available Credits",
      value=f"**${user_data['balance']:.2f} USD**",
      inline=True,
  )
  embed.add_field(
      name="Subscription Tier", value=user_data["tier"], inline=True
  )
  embed.set_footer(
      text="Credits auto-burn during active GPU rentals at $0.30/hr."
  )

  await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="nodes", description="List all active GPU host nodes in the network."
)
async def nodes(interaction: discord.Interaction):
  embed = discord.Embed(
      title="🖥️ ComputeX Host Network Status", color=discord.Color.green()
  )

  if not db["nodes"]:
    embed.description = (
        "No host nodes registered yet. Run `install.sh` on a GPU machine to"
        " register!"
    )
  else:
    for node_id, data in db["nodes"].items():
      status_emoji = "🟢" if data.get("status") == "ONLINE" else "🔴"
      embed.add_field(
          name=f"{status_emoji} Node: {node_id}",
          value=(
              f"**GPU:** {data.get('gpu', 'Unknown')}\n"
              f"**VRAM:** {data.get('vram', 'N/A')}\n"
              f"**Rate:** ${data.get('rate', 0.30)}/hr\n"
              f"**Mode:** {data.get('mode', 'Docker/SSH')}"
          ),
          inline=True,
      )

  await interaction.response.send_message(embed=embed)


class TemplateSelect(discord.ui.Select):

  def __init__(self, available_node_id):
    self.node_id = available_node_id
    options = [
        discord.SelectOption(
            label="🎨 ComfyUI (Hobbyist)",
            description=(
                "1-Click WebUI for AI Image Generation (No Code Required)"
            ),
            value="comfyui",
        ),
        discord.SelectOption(
            label="📓 JupyterLab Workspace (Hobbyist/Dev)",
            description="Interactive Python notebook environment in browser",
            value="jupyter",
        ),
        discord.SelectOption(
            label="🦙 Ollama Local LLM Server",
            description=(
                "Pre-configured local AI API endpoint for text generation"
            ),
            value="ollama",
        ),
        discord.SelectOption(
            label="💻 Raw Reverse SSH Tunnel (Developer)",
            description="Full root terminal command-line access via tmate",
            value="ssh",
        ),
    ]
    super().__init__(
        placeholder="Select your environment template...", options=options
    )

  async def callback(self, interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    template_choice = self.values[0]

    session_id = f"session_{int(time.time())}"
    db["active_sessions"][session_id] = {
        "user_id": user_id,
        "node_id": self.node_id,
        "template": template_choice,
        "start_time": time.time(),
        "rate_per_sec": 0.30 / 3600.0,
    }
    db["nodes"][self.node_id]["status"] = "RENTED"
    save_db(db)

    embed = discord.Embed(
        title="🚀 GPU Rental Session Launched!", color=discord.Color.gold()
    )
    embed.add_field(name="Assigned Node", value=self.node_id, inline=True)
    embed.add_field(
        name="Selected Preset", value=template_choice.upper(), inline=True
    )

    if template_choice == "ssh":
      embed.add_field(
          name="🔑 Connection SSH String",
          value="`ssh tmate-tunnel-string-placeholder@tmate.io`",
          inline=False,
      )
      embed.add_field(
          name="Instructions",
          value="Paste the SSH command into your Mac/Windows terminal.",
          inline=False,
      )
    else:
      embed.add_field(
          name="🌐 1-Click Browser Link",
          value=(
              f"[Click Here to Open"
              f" {template_choice.upper()}](https://{self.node_id}.computex.proxy:8888)"
          ),
          inline=False,
      )
      embed.add_field(
          name="Instructions",
          value=(
              "Click the link to open your ready-to-use AI dashboard in your"
              " browser."
          ),
          inline=False,
      )

    embed.set_footer(
        text=(
            "Use /stop_rental to terminate your session and preserve unused"
            " credits."
        )
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


class TemplateView(discord.ui.View):

  def __init__(self, available_node_id):
    super().__init__()
    self.add_item(TemplateSelect(available_node_id))


@bot.tree.command(
    name="rent_gpu", description="Rent an RTX 3090/4090 GPU node instantly."
)
async def rent_gpu(interaction: discord.Interaction):
  user_id = str(interaction.user.id)
  user_data = db["users"].get(user_id, {"balance": 0.00})

  if user_data.get("balance", 0.00) < 0.50:
    await interaction.response.send_message(
        "❌ **Insufficient Credits!** You need at least $0.50 in compute credits"
        " to start a rental. Upgrade your plan on Whop to add credits.",
        ephemeral=True,
    )
    return

  available_node = None
  for n_id, n_data in db["nodes"].items():
    if n_data.get("status") == "ONLINE":
      available_node = n_id
      break

  if not available_node:
    await interaction.response.send_message(
        "🔴 **No host nodes currently available!** All GPUs are currently rented"
        " or offline. Please check back shortly.",
        ephemeral=True,
    )
    return

  view = TemplateView(available_node)
  await interaction.response.send_message(
      "⚡ **Select your workspace preset:**", view=view, ephemeral=True
  )


@bot.tree.command(
    name="stop_rental", description="Stop your active GPU rental session."
)
async def stop_rental(interaction: discord.Interaction):
  user_id = str(interaction.user.id)
  active_session_id = None
  session_data = None

  for s_id, s_data in db["active_sessions"].items():
    if s_data.get("user_id") == user_id:
      active_session_id = s_id
      session_data = s_data
      break

  if not active_session_id:
    await interaction.response.send_message(
        "❌ You have no active GPU rental sessions.", ephemeral=True
    )
    return

  runtime_seconds = time.time() - session_data["start_time"]
  total_cost = runtime_seconds * session_data["rate_per_sec"]

  db["users"][user_id]["balance"] -= total_cost
  node_id = session_data["node_id"]
  if node_id in db["nodes"]:
    db["nodes"][node_id]["status"] = "ONLINE"

  del db["active_sessions"][active_session_id]
  save_db(db)

  embed = discord.Embed(
      title="🛑 Rental Session Terminated", color=discord.Color.red()
  )
  embed.add_field(
      name="Duration", value=f"{int(runtime_seconds // 60)} minutes", inline=True
  )
  embed.add_field(name="Total Cost", value=f"${total_cost:.4f} USD", inline=True)
  embed.add_field(
      name="Remaining Balance",
      value=f"${db['users'][user_id]['balance']:.2f} USD",
      inline=True,
  )

  await interaction.response.send_message(embed=embed, ephemeral=True)


# =======================================================
# 5. BACKGROUND ENGINE & STARTUP
# =======================================================
@tasks.loop(seconds=60)
async def meter_active_sessions():
  sessions_to_kill = []
  for s_id, s_data in list(db["active_sessions"].items()):
    user_id = s_data.get("user_id")
    minute_cost = s_data.get("rate_per_sec", 0.30 / 3600.0) * 60.0

    if user_id in db["users"]:
      db["users"][user_id]["balance"] -= minute_cost
      if db["users"][user_id]["balance"] <= 0:
        db["users"][user_id]["balance"] = 0.0
        sessions_to_kill.append((s_id, user_id, s_data.get("node_id")))

  for s_id, u_id, n_id in sessions_to_kill:
    if n_id in db["nodes"]:
      db["nodes"][n_id]["status"] = "ONLINE"
    if s_id in db["active_sessions"]:
      del db["active_sessions"][s_id]

  save_db(db)


if __name__ == "__main__":
  if not TOKEN:
    raise ValueError(
        "DISCORD_BOT_TOKEN environment variable is missing! Check Render"
        " Environment settings."
    )
  bot.run(TOKEN)
