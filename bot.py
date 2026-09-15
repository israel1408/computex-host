import os
import sys
import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
import discord
from discord import app_commands
from discord.ext import commands, tasks

# ---------------------------------------------------------
# Configuration & Persistence Setup
# ---------------------------------------------------------
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
HOST_API_KEY = os.getenv("HOST_API_KEY", "default-secret-key")
PORT = int(os.getenv("PORT", "8080"))

# Determine DB path (supports Render Persistent Disk at /data if mounted)
DB_DIR = "/data" if os.path.exists("/data") else "."
DB_FILE = os.path.join(DB_DIR, "database.json")

def load_db():
    if not os.path.exists(DB_FILE):
        return {"nodes": {}, "users": {}, "rentals": {}}
    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"nodes": {}, "users": {}, "rentals": {}}

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ---------------------------------------------------------
# HTTP Control Plane Server
# ---------------------------------------------------------
class ComputeXHTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ComputeX Control Plane Active")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        db = load_db()
        
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        # Safely parse JSON payload with try...except guard
        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except json.JSONDecodeError:
            self.send_response(400)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Invalid JSON Payload")
            return

        # Endpoint 1: Register Node
        if self.path == "/register-node":
            auth_header = self.headers.get("X-Host-API-Key")
            if auth_header != HOST_API_KEY:
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"Unauthorized")
                return

            node_id = data.get("node_id")
            if not node_id:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing node_id")
                return

            # Record or update host node
            existing_status = db["nodes"].get(node_id, {}).get("status", "ONLINE")
            db["nodes"][node_id] = {
                "node_id": node_id,
                "gpu_name": data.get("gpu_name", "Unknown GPU"),
                "vram": data.get("vram", "N/A"),
                "hourly_rate": float(data.get("hourly_rate", 0.30)),
                "ssh_cmd": data.get("ssh_cmd", ""),
                "web_cmd": data.get("web_cmd", ""),
                "status": existing_status,
                "last_seen": time.time()
            }
            save_db(db)
            print(f"🟢 Node Registered: {node_id}")

            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Registered")
            return

        # Endpoint 2: Whop Webhook
        elif self.path == "/whop-webhook":
            action = data.get("action")
            if action == "payment.succeeded":
                payment_data = data.get("data", {})
                
                # Extract amount (Whop sends cents)
                amount_cents = payment_data.get("final_amount", 0)
                amount_usd = amount_cents / 100.0

                # Extract Discord User ID with Whop fallback
                user_data = payment_data.get("user", {})
                custom_fields = payment_data.get("custom_fields", {})
                discord_id = (
                    custom_fields.get("discord_user_id") or
                    user_data.get("social_accounts", {}).get("discord", {}).get("id") or
                    user_data.get("discord_id")
                )

                if discord_id:
                    discord_id_str = str(discord_id)
                    current_bal = db["users"].get(discord_id_str, {}).get("balance", 0.0)
                    db["users"][discord_id_str] = {
                        "balance": round(current_bal + amount_usd, 2)
                    }
                    save_db(db)
                    print(f"✅ Credited ${amount_usd:.2f} USD to Discord User {discord_id_str}")

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
            return

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Silence standard HTTP logging to keep Render console clean
        return

def run_http_server():
    server_address = ("", PORT)
    httpd = HTTPServer(server_address, ComputeXHTTPHandler)
    print(f"🌐 HTTP Control Plane Server running on port {PORT}")
    httpd.serve_forever()

# ---------------------------------------------------------
# Discord Bot Setup
# ---------------------------------------------------------
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} Slash Commands")
    except Exception as e:
        print(f"❌ Failed to sync slash commands: {e}")
    print(f"⚡ ComputeX Clearhouse Bot Active as {bot.user}")
    if not node_pruner.is_running():
        node_pruner.start()

# ---------------------------------------------------------
# Background Task: Prune Inactive Nodes (120s timeout)
# ---------------------------------------------------------
@tasks.loop(seconds=30)
async def node_pruner():
    db = load_db()
    now = time.time()
    updated = False
    for node_id, node in db.get("nodes", {}).items():
        if node.get("status") == "ONLINE" and (now - node.get("last_seen", 0)) > 120:
            node["status"] = "OFFLINE"
            updated = True
            print(f"⚠️ Node {node_id} marked OFFLINE due to inactivity.")
    if updated:
        save_db(db)

# ---------------------------------------------------------
# Slash Commands
# ---------------------------------------------------------
@bot.tree.command(name="balance", description="Check your ComputeX account balance")
async def balance_cmd(interaction: discord.Interaction):
    db = load_db()
    user_id = str(interaction.user.id)
    bal = db["users"].get(user_id, {}).get("balance", 0.0)
    await interaction.response.send_message(
        f"💳 **Your Balance:** `${bal:.2f} USD`", ephemeral=True
    )

@bot.tree.command(name="nodes", description="List all available GPU compute nodes")
async def nodes_cmd(interaction: discord.Interaction):
    db = load_db()
    nodes = db.get("nodes", {})
    if not nodes:
        await interaction.response.send_message("No nodes registered yet.", ephemeral=True)
        return

    lines = ["**Available GPU Compute Nodes:**\n"]
    for nid, info in nodes.items():
        status_emoji = "🟢" if info["status"] == "ONLINE" else "🔴" if info["status"] == "BUSY" else "⚪"
        lines.append(
            f"{status_emoji} **{nid}** | {info['gpu_name']} ({info['vram']}) | `${info['hourly_rate']:.2f}/hr` | Status: `{info['status']}`"
        )
    await interaction.response.send_message("\n".join(lines), ephemeral=True)

@bot.tree.command(name="rent_gpu", description="Rent an available GPU node")
async def rent_gpu_cmd(interaction: discord.Interaction):
    db = load_db()
    user_id = str(interaction.user.id)
    bal = db["users"].get(user_id, {}).get("balance", 0.0)

    if bal <= 0:
        await interaction.response.send_message(
            "❌ **Insufficient Balance.** Please top up via Whop to rent a GPU.", ephemeral=True
        )
        return

    if user_id in db.get("rentals", {}):
        await interaction.response.send_message(
            "⚠️ You already have an active rental! Run `/stop_rental` first.", ephemeral=True
        )
        return

    target_node = None
    for nid, info in db.get("nodes", {}).items():
        if info["status"] == "ONLINE":
            target_node = info
            break

    if not target_node:
        await interaction.response.send_message(
            "❌ No `ONLINE` GPU nodes available right now. Try again shortly!", ephemeral=True
        )
        return

    target_node["status"] = "BUSY"
    db["rentals"][user_id] = {
        "node_id": target_node["node_id"],
        "start_time": time.time(),
        "hourly_rate": target_node["hourly_rate"]
    }
    save_db(db)

    msg = (
        f"🚀 **GPU Rental Started!**\n"
        f"• **Node:** `{target_node['node_id']}` ({target_node['gpu_name']})\n"
        f"• **Rate:** `${target_node['hourly_rate']:.2f}/hr`\n\n"
        f"**Access Details:**\n"
        f"• SSH: `{target_node['ssh_cmd']}`\n"
        f"• Web Terminal: {target_node['web_cmd']}\n\n"
        f"Run `/stop_rental` when finished to stop per-second billing."
    )
    await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="stop_rental", description="Stop your active GPU rental and finalize billing")
async def stop_rental_cmd(interaction: discord.Interaction):
    db = load_db()
    user_id = str(interaction.user.id)

    rental = db.get("rentals", {}).get(user_id)
    if not rental:
        await interaction.response.send_message("❌ You do not have an active rental.", ephemeral=True)
        return

    duration_sec = time.time() - rental["start_time"]
    per_sec_rate = rental["hourly_rate"] / 3600.0
    cost = duration_sec * per_sec_rate

    user_bal = db["users"].get(user_id, {}).get("balance", 0.0)
    new_bal = max(0.0, user_bal - cost)
    db["users"][user_id] = {"balance": round(new_bal, 4)}

    node_id = rental["node_id"]
    if node_id in db.get("nodes", {}):
        db["nodes"][node_id]["status"] = "ONLINE"

    del db["rentals"][user_id]
    save_db(db)

    await interaction.response.send_message(
        f"🛑 **Rental Terminated.**\n"
        f"• **Duration:** `{int(duration_sec)} seconds`\n"
        f"• **Total Cost:** `${cost:.4f} USD`\n"
        f"• **Remaining Balance:** `${new_bal:.2f} USD`\n"
        f"Node `{node_id}` is back `ONLINE`.",
        ephemeral=True
    )

# ---------------------------------------------------------
# Main Application Entrypoint
# ---------------------------------------------------------
if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN:
        print("❌ FATAL: DISCORD_BOT_TOKEN Environment Variable is missing!")
        sys.exit(1)

    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()

    bot.run(DISCORD_BOT_TOKEN)
