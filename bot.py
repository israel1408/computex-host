import os
import json
import asyncio
import hmac
import hashlib
import base64
from datetime import datetime
from aiohttp import web
import discord
from discord import app_commands
from discord.ext import commands, tasks

# --- CONFIGURATION & SECURITY KEYS ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")
HOST_API_KEY = os.getenv("HOST_API_KEY")
WHOP_WEBHOOK_SECRET = os.getenv("WHOP_WEBHOOK_SECRET")
DATA_FILE = "/data/database.json" if os.path.exists("/data") else "database.json"

if not HOST_API_KEY or len(HOST_API_KEY) < 16:
    print("⚠️ WARNING: HOST_API_KEY is short or missing. Set a secure 16+ character key in production.")

rental_lock = asyncio.Lock()

# --- DATABASE MANAGEMENT ---
def load_db():
    if not os.path.exists(DATA_FILE):
        return {"nodes": {}, "users": {}, "rentals": {}, "processed_webhooks": []}
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            data.setdefault("processed_webhooks", [])
            data.setdefault("nodes", {})
            data.setdefault("users", {})
            data.setdefault("rentals", {})
            return data
    except Exception:
        return {"nodes": {}, "users": {}, "rentals": {}, "processed_webhooks": []}

def save_db(data):
    temp_file = f"{DATA_FILE}.tmp"
    with open(temp_file, "w") as f:
        json.dump(data, f, indent=4)
    os.replace(temp_file, DATA_FILE)

db = load_db()

# --- WEBHOOK SIGNATURE VERIFICATION ---
def verify_whop_signature(raw_body: bytes, headers: dict, secret: str) -> bool:
    webhook_id = headers.get("webhook-id")
    webhook_timestamp = headers.get("webhook-timestamp")
    webhook_signature = headers.get("webhook-signature")

    if not all([webhook_id, webhook_timestamp, webhook_signature, secret]):
        return False

    signed_payload = f"{webhook_id}.{webhook_timestamp}.{raw_body.decode('utf-8')}".encode('utf-8')
    computed_hmac = hmac.new(secret.encode('utf-8'), signed_payload, hashlib.sha256).digest()
    expected_sig = base64.b64encode(computed_hmac).decode('utf-8')

    signatures = [s.replace("v1,", "").strip() for s in webhook_signature.split(" ")]
    return any(hmac.compare_digest(sig, expected_sig) for sig in signatures)

# --- HTTP CONTROL PLANE (AIOHTTP) ---
async def handle_health(request):
    return web.Response(text="ComputeX Control Plane Active", status=200)

async def handle_register_node(request):
    if request.headers.get("X-Host-API-Key") != HOST_API_KEY:
        return web.json_response({"error": "Unauthorized key"}, status=401)
    
    try:
        data = await request.json()
        node_id = data.get("node_id")
        price = data.get("price_per_hour") or data.get("hourly_rate") or 0.30
        
        if not node_id:
            return web.json_response({"error": "Invalid payload format"}, status=400)
            
        db["nodes"][node_id] = {
            "node_id": node_id,
            "gpu_model": str(data.get("gpu_model") or data.get("gpu_name") or "Unknown GPU"),
            "vram": str(data.get("vram", "N/A")),
            "price_per_hour": float(price),
            "ssh_connection": str(data.get("ssh_connection") or data.get("ssh_cmd") or ""),
            "web_ui_url": str(data.get("web_ui_url") or data.get("web_cmd") or ""),
            "status": "available",
            "last_heartbeat": datetime.utcnow().isoformat()
        }
        save_db(db)
        print(f"🟢 Node Registered: {node_id}")
        return web.json_response({"status": "registered", "node_id": node_id})
    except Exception as e:
        return web.json_response({"error": f"Server error: {str(e)}"}, status=500)

async def handle_heartbeat(request):
    if request.headers.get("X-Host-API-Key") != HOST_API_KEY:
        return web.json_response({"error": "Unauthorized"}, status=401)
    try:
        data = await request.json()
        node_id = data.get("node_id")
        if not node_id or node_id not in db["nodes"]:
            return web.json_response({"error": "Node not found"}, status=404)

        db["nodes"][node_id]["last_heartbeat"] = datetime.utcnow().isoformat()
        if db["nodes"][node_id].get("status") == "offline":
            db["nodes"][node_id]["status"] = "available"
        save_db(db)
        return web.json_response({"status": "alive"}, status=200)
    except Exception:
        return web.json_response({"error": "Invalid payload"}, status=400)

async def handle_whop_webhook(request):
    raw_body = await request.read()
    headers = request.headers

    if WHOP_WEBHOOK_SECRET and not verify_whop_signature(raw_body, headers, WHOP_WEBHOOK_SECRET):
        return web.json_response({"error": "Invalid webhook signature"}, status=401)

    try:
        payload = json.loads(raw_body)
        webhook_id = headers.get("webhook-id") or payload.get("id")

        if webhook_id and webhook_id in db["processed_webhooks"]:
            return web.json_response({"status": "already_processed"}, status=200)

        event_type = payload.get("action") or payload.get("type")
        if event_type == "payment.succeeded":
            data = payload.get("data", {})
            user_data = data.get("user", {})
            custom_fields = data.get("custom_fields", {})
            discord_id = (
                custom_fields.get("discord_user_id") or
                user_data.get("social_accounts", {}).get("discord", {}).get("id") or
                user_data.get("discord_id")
            )
            amount = float(data.get("final_amount", 0)) / 100.0

            if discord_id and amount > 0:
                discord_id_str = str(discord_id)
                current_bal = db["users"].get(discord_id_str, {}).get("balance", 0.0)
                db["users"][discord_id_str] = {"balance": round(current_bal + amount, 2)}
                
                if webhook_id:
                    db["processed_webhooks"].append(webhook_id)
                    if len(db["processed_webhooks"]) > 1000:
                        db["processed_webhooks"].pop(0)
                
                save_db(db)
                print(f"✅ Credited ${amount:.2f} USD to Discord User {discord_id_str}")

        return web.json_response({"status": "success"}, status=200)
    except Exception as e:
        return web.json_response({"error": "Malformed payload"}, status=400)

# --- DISCORD BOT SETUP ---
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- BACKGROUND BILLING & PRUNING WORKER ---
@tasks.loop(seconds=30)
async def billing_loop():
    async with rental_lock:
        now = datetime.utcnow()
        to_stop = []

        # 1. Prune nodes missing heartbeats (> 120s)
        for nid, ninfo in list(db["nodes"].items()):
            last_hb_str = ninfo.get("last_heartbeat")
            if last_hb_str:
                try:
                    last_hb = datetime.fromisoformat(last_hb_str)
                    if (now - last_hb).total_seconds() > 120 and ninfo["status"] != "offline":
                        ninfo["status"] = "offline"
                        print(f"⚠️ Node {nid} marked OFFLINE due to missed heartbeats.")
                except Exception:
                    pass

        # 2. Process per-30-second balance deductions for active rentals
        for rental_id, rental in list(db["rentals"].items()):
            user_id = rental["user_id"]
            node_id = rental["node_id"]
            price_per_min = rental["price_per_hour"] / 60.0
            
            cost_for_30s = price_per_min * 0.5
            user_balance = db["users"].get(user_id, {}).get("balance", 0.0)

            if user_balance < cost_for_30s:
                to_stop.append((rental_id, user_id, node_id))
            else:
                db["users"][user_id]["balance"] = round(user_balance - cost_for_30s, 4)

        for rental_id, user_id, node_id in to_stop:
            del db["rentals"][rental_id]
            if node_id in db["nodes"]:
                db["nodes"][node_id]["status"] = "available"
            
            try:
                user = await bot.fetch_user(int(user_id))
                if user:
                    await user.send(f"⚠️ **Rental Terminated**: Your session on `{node_id}` was automatically stopped due to zero remaining balance.")
            except Exception:
                pass

        save_db(db)

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} Slash Commands")
    except Exception as e:
        print(f"❌ Failed to sync slash commands: {e}")
    print(f"⚡ ComputeX Clearhouse Bot Active as {bot.user}")
    if not billing_loop.is_running():
        billing_loop.start()

# --- SLASH COMMANDS ---
@bot.tree.command(name="balance", description="Check your ComputeX account balance")
async def balance_cmd(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    bal = db["users"].get(user_id, {}).get("balance", 0.0)
    await interaction.response.send_message(f"💳 **Your Balance:** `${bal:.2f} USD`", ephemeral=True)

@bot.tree.command(name="nodes", description="List all available GPU compute nodes")
async def nodes_cmd(interaction: discord.Interaction):
    nodes = db.get("nodes", {})
    if not nodes:
        await interaction.response.send_message("No nodes registered yet.", ephemeral=True)
        return

    lines = ["**Available GPU Compute Nodes:**\n"]
    for nid, info in nodes.items():
        status = info.get("status", "available")
        status_emoji = "🟢" if status in ("available", "ONLINE") else "🔴" if status in ("occupied", "BUSY") else "⚪"
        gpu_name = info.get("gpu_model") or info.get("gpu_name") or "Unknown GPU"
        vram = info.get("vram", "N/A")
        rate = info.get("price_per_hour") or info.get("hourly_rate") or 0.30
        lines.append(f"{status_emoji} **{nid}** | {gpu_name} ({vram}) | `${rate:.2f}/hr` | Status: `{status}`")
    
    await interaction.response.send_message("\n".join(lines), ephemeral=True)

@bot.tree.command(name="rent_gpu", description="Rent an available GPU node")
async def rent_gpu_cmd(interaction: discord.Interaction):
    async with rental_lock:
        user_id = str(interaction.user.id)
        bal = db["users"].get(user_id, {}).get("balance", 0.0)

        for r_id, r_info in db.get("rentals", {}).items():
            if r_info.get("user_id") == user_id:
                await interaction.response.send_message("⚠️ You already have an active rental! Run `/stop_rental` first.", ephemeral=True)
                return

        target_node = None
        for nid, info in db.get("nodes", {}).items():
            if info.get("status") in ("available", "ONLINE"):
                target_node = info
                break

        if not target_node:
            await interaction.response.send_message("❌ No available GPU nodes right now. Try again shortly!", ephemeral=True)
            return

        rate = target_node.get("price_per_hour") or target_node.get("hourly_rate") or 0.30
        if bal < (rate / 60.0 * 5):
            await interaction.response.send_message("❌ Insufficient balance! Please top up via Whop to rent a GPU.", ephemeral=True)
            return

        target_node["status"] = "occupied"
        rental_id = f"{user_id}_{target_node['node_id']}_{int(datetime.utcnow().timestamp())}"
        db["rentals"][rental_id] = {
            "user_id": user_id,
            "node_id": target_node["node_id"],
            "price_per_hour": rate,
            "start_time": datetime.utcnow().isoformat()
        }
        save_db(db)

        gpu_name = target_node.get("gpu_model") or target_node.get("gpu_name")
        ssh_cmd = target_node.get("ssh_connection") or target_node.get("ssh_cmd") or "N/A"
        web_ui = target_node.get("web_ui_url") or target_node.get("web_cmd") or "N/A"

        await interaction.response.send_message(
            f"🚀 **GPU Rental Started!**\n"
            f"• **Node:** `{target_node['node_id']}` ({gpu_name})\n"
            f"• **Rate:** `${rate:.2f}/hr`\n\n"
            f"**Access Details:**\n"
            f"• SSH: `{ssh_cmd}`\n"
            f"• Web Terminal: {web_ui}\n\n"
            f"Run `/stop_rental` when finished to stop billing.",
            ephemeral=True
        )

@bot.tree.command(name="stop_rental", description="Stop your active GPU rental")
async def stop_rental_cmd(interaction: discord.Interaction):
    async with rental_lock:
        user_id = str(interaction.user.id)
        
        active_rental_id = None
        active_rental = None
        for r_id, r_info in db.get("rentals", {}).items():
            if r_info.get("user_id") == user_id:
                active_rental_id = r_id
                active_rental = r_info
                break

        if not active_rental:
            await interaction.response.send_message("❌ You do not have an active rental.", ephemeral=True)
            return

        node_id = active_rental["node_id"]
        if node_id in db.get("nodes", {}):
            db["nodes"][node_id]["status"] = "available"

        del db["rentals"][active_rental_id]
        save_db(db)

        user_bal = db["users"].get(user_id, {}).get("balance", 0.0)
        await interaction.response.send_message(
            f"🛑 **Rental Terminated.**\n"
            f"Node `{node_id}` is now available.\n"
            f"• **Remaining Balance:** `${user_bal:.2f} USD`",
            ephemeral=True
        )

# --- STARTUP ENTRYPOINT ---
async def main():
    if not DISCORD_TOKEN:
        raise ValueError("FATAL: DISCORD_TOKEN / DISCORD_BOT_TOKEN environment variable is missing.")

    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/register-node", handle_register_node)
    app.router.add_post("/heartbeat", handle_heartbeat)
    app.router.add_post("/whop-webhook", handle_whop_webhook)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080)))
    await site.start()
    print(f"🌐 HTTP Control Plane Server running on port {os.getenv('PORT', 8080)}")

    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
