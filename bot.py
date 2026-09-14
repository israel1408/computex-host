import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
import time
import requests
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# =======================================================
# CONFIGURATION & DATABASE SETUP
# =======================================================
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
bot.run(TOKEN)
DATABASE_FILE = "computex_db.json"

class HealthCheckHandler(BaseHTTPRequestHandler):

  def do_GET(self):
    self.send_response(200)
    self.end_headers()
    self.wfile.write(b"OK")


def run_health_check():
  port = int(os.getenv("PORT", 8080))
  server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
  server.serve_forever()


threading.Thread(target=run_health_check, daemon=True).start()
# In-Memory Database Structure with Persistent Disk Sync
def load_db():
    if not os.path.exists(DATABASE_FILE):
        return {"users": {}, "nodes": {}, "active_sessions": {}}
    with open(DATABASE_FILE, "r") as f:
        return json.load(f)

def save_db(db):
    with open(DATABASE_FILE, "w") as f:
        json.dump(db, f, indent=4)

db = load_db()

# =======================================================
# BOT INITIALIZATION
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
    
    # Start background billing engine
    meter_active_sessions.start()

# =======================================================
# SLASH COMMAND 1: /balance (Check Credits)
# =======================================================
@bot.tree.command(name="balance", description="Check your remaining ComputeX credit balance.")
async def balance(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    user_data = db["users"].get(user_id, {"balance": 0.00, "tier": "Free"})
    
    embed = discord.Embed(
        title="💳 ComputeX Account Balance",
        color=discord.Color.blue()
    )
    embed.add_field(name="Available Credits", value=f"**${user_data['balance']:.2f} USD**", inline=True)
    embed.add_field(name="Subscription Tier", value=user_data['tier'], inline=True)
    embed.set_footer(text="Credits auto-burn during active GPU rentals at $0.30/hr.")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# =======================================================
# SLASH COMMAND 2: /nodes (View Available GPUs)
# =======================================================
@bot.tree.command(name="nodes", description="List all active GPU host nodes in the network.")
async def nodes(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🖥️ ComputeX Host Network Status",
        color=discord.Color.green()
    )
    
    if not db["nodes"]:
        embed.description = "No host nodes registered yet. Run `install.sh` on a GPU machine to register!"
    else:
        for node_id, data in db["nodes"].items():
            status_emoji = "🟢" if data["status"] == "ONLINE" else "🔴"
            embed.add_field(
                name=f"{status_emoji} Node: {node_id}",
                value=f"**GPU:** {data['gpu']}\n**VRAM:** {data['vram']}\n**Rate:** ${data['rate']}/hr\n**Mode:** {data['mode']}",
                inline=True
            )
            
    await interaction.response.send_message(embed=embed)

# =======================================================
# SLASH COMMAND 3: /rent_gpu (Hobbyist & Dev Launcher)
# =======================================================
class TemplateSelect(discord.ui.Select):
    def __init__(self, available_node_id):
        self.node_id = available_node_id
        options = [
            discord.SelectOption(
                label="🎨 ComfyUI (Hobbyist)",
                description="1-Click WebUI for AI Image Generation (No Code Required)",
                value="comfyui"
            ),
            discord.SelectOption(
                label="📓 JupyterLab Workspace (Hobbyist/Dev)",
                description="Interactive Python notebook environment in browser",
                value="jupyter"
            ),
            discord.SelectOption(
                label="🦙 Ollama Local LLM Server",
                description="Pre-configured local AI API endpoint for text generation",
                value="ollama"
            ),
            discord.SelectOption(
                label="💻 Raw Reverse SSH Tunnel (Developer)",
                description="Full root terminal command-line access via tmate",
                value="ssh"
            )
        ]
        super().__init__(placeholder="Select your environment template...", options=options)

    async def callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        template_choice = self.values[0]
        
        # Register active session
        session_id = f"session_{int(time.time())}"
        db["active_sessions"][session_id] = {
            "user_id": user_id,
            "node_id": self.node_id,
            "template": template_choice,
            "start_time": time.time(),
            "rate_per_sec": 0.30 / 3600.0  # $0.30 per hour
        }
        db["nodes"][self.node_id]["status"] = "RENTED"
        save_db(db)

        # Dispatch connection details based on target persona
        embed = discord.Embed(
            title="🚀 GPU Rental Session Launched!",
            color=discord.Color.gold()
        )
        embed.add_field(name="Assigned Node", value=self.node_id, inline=True)
        embed.add_field(name="Selected Preset", value=template_choice.upper(), inline=True)
        
        if template_choice == "ssh":
            embed.add_field(
                name="🔑 Connection SSH String",
                value="`ssh tmate-tunnel-string-placeholder@tmate.io`",
                inline=False
            )
            embed.add_field(name="Instructions", value="Paste the SSH command into your Mac/Windows terminal.", inline=False)
        else:
            embed.add_field(
                name="🌐 1-Click Browser Link",
                value=f"[Click Here to Open {template_choice.upper()}](https://{self.node_id}.computex.proxy:8888)",
                inline=False
            )
            embed.add_field(name="Instructions", value="Click the link to open your ready-to-use AI dashboard in your browser.", inline=False)
            
        embed.set_footer(text="Use /stop_rental to terminate your session and preserve unused credits.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

class TemplateView(discord.ui.View):
    def __init__(self, available_node_id):
        super().__init__()
        self.add_item(TemplateSelect(available_node_id))

@bot.tree.command(name="rent_gpu", description="Rent an RTX 3090/4090 GPU node instantly.")
async def rent_gpu(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    user_data = db["users"].get(user_id, {"balance": 0.00})
    
    # Check minimum balance ($0.50)
    if user_data["balance"] < 0.50:
        await interaction.response.send_message(
            "❌ **Insufficient Credits!** You need at least $0.50 in compute credits to start a rental. Upgrade your plan on Whop to add credits.",
            ephemeral=True
        )
        return

    # Find an available host node
    available_node = None
    for n_id, n_data in db["nodes"].items():
        if n_data["status"] == "ONLINE":
            available_node = n_id
            break

    if not available_node:
        await interaction.response.send_message(
            "🔴 **No host nodes currently available!** All GPUs are currently rented or offline. Please check back shortly.",
            ephemeral=True
        )
        return

    view = TemplateView(available_node)
    await interaction.response.send_message("⚡ **Select your workspace preset:**", view=view, ephemeral=True)

# =======================================================
# SLASH COMMAND 4: /stop_rental (End Session & Print Receipt)
# =======================================================
@bot.tree.command(name="stop_rental", description="Stop your active GPU rental session.")
async def stop_rental(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    active_session_id = None
    session_data = None

    for s_id, s_data in db["active_sessions"].items():
        if s_data["user_id"] == user_id:
            active_session_id = s_id
            session_data = s_data
            break

    if not active_session_id:
        await interaction.response.send_message("❌ You have no active GPU rental sessions.", ephemeral=True)
        return

    # Calculate final runtime burn
    runtime_seconds = time.time() - session_data["start_time"]
    total_cost = runtime_seconds * session_data["rate_per_sec"]
    
    # Deduct credits
    db["users"][user_id]["balance"] -= total_cost
    node_id = session_data["node_id"]
    if node_id in db["nodes"]:
        db["nodes"][node_id]["status"] = "ONLINE"
    
    del db["active_sessions"][active_session_id]
    save_db(db)

    embed = discord.Embed(
        title="🛑 Rental Session Terminated",
        color=discord.Color.red()
    )
    embed.add_field(name="Duration", value=f"{int(runtime_seconds // 60)} minutes", inline=True)
    embed.add_field(name="Total Cost", value=f"${total_cost:.4f} USD", inline=True)
    embed.add_field(name="Remaining Balance", value=f"${db['users'][user_id]['balance']:.2f} USD", inline=True)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# =======================================================
# BACKGROUND ENGINE: Real-Time Credit Metering (60s Loop)
# =======================================================
@tasks.loop(seconds=60)
async def meter_active_sessions():
    """Deducts compute credits every minute and auto-kills sessions if balance reaches $0."""
    sessions_to_kill = []

    for s_id, s_data in db["active_sessions"].items():
        user_id = s_data["user_id"]
        minute_cost = s_data["rate_per_sec"] * 60.0

        if user_id in db["users"]:
            db["users"][user_id]["balance"] -= minute_cost
            
            # Auto-kill if balance depleted
            if db["users"][user_id]["balance"] <= 0:
                db["users"][user_id]["balance"] = 0.0
                sessions_to_kill.append((s_id, user_id, s_data["node_id"]))

    for s_id, u_id, n_id in sessions_to_kill:
        if n_id in db["nodes"]:
            db["nodes"][n_id]["status"] = "ONLINE"
        del db["active_sessions"][s_id]
        print(f"[!] Session {s_id} auto-terminated due to zero credit balance.")

    save_db(db)

# =======================================================
# RUN BOT ENGINE
# =======================================================
if __name__ == "__main__":
    print(f"Loaded Token Length: {len(TOKEN) if TOKEN else 'NONE'}")
    bot.run(TOKEN)
