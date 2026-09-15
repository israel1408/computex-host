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
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
HOST_API_KEY = os.getenv("HOST_API_KEY")  # Key required for nodes to register/heartbeat
WHOP_WEBHOOK_SECRET = os.getenv("WHOP_WEBHOOK_SECRET")  # Secret from Whop developer console
DATA_FILE = "/data/database.json"

if not HOST_API_KEY or len(HOST_API_KEY) < 16:
    raise ValueError("SECURITY WARNING: HOST_API_KEY must be set and at least 16 characters long.")

# Lock to prevent race conditions during rentals
rental_lock = asyncio.Lock()

# --- DATABASE MANAGEMENT ---
def load_db():
    if not os.path.exists(DATA_FILE):
        return {"nodes": {}, "users": {}, "rentals": {}, "processed_webhooks": []}
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            data.setdefault("processed_webhooks", [])
            return data
    except Exception:
        return {"nodes": {}, "users": {}, "rentals": {}, "processed_webhooks": []}

def save_db(data):
    # Atomic write to prevent file corruption
    temp_file = f"{DATA_FILE}.tmp"
    with open(temp_file, "w") as f:
        json.dump(data, f, indent=4)
    os.replace(temp_file, DATA_FILE)

db = load_db()

# --- WEBHOOK SIGNATURE VERIFICATION ---
def verify_whop_signature(raw_body: bytes, headers: dict, secret: str) -> bool:
    """Verifies HMAC SHA256 signature from Whop Standard Webhooks."""
    webhook_id = headers.get("webhook-id")
    webhook_timestamp = headers.get("webhook-timestamp")
    webhook_signature = headers.get("webhook-signature")

    if not all([webhook_id, webhook_timestamp, webhook_signature, secret]):
        return False

    # Standard Webhook Signature Format: {id}.{timestamp}.{raw_body}
    signed_payload = f"{webhook_id}.{webhook_timestamp}.{raw_body.decode('utf-8')}".encode('utf-8')
    computed_hmac = hmac.new(secret.encode('utf-8'), signed_payload, hashlib.sha256).digest()
    expected_sig = base64.b64encode(computed_hmac).decode('utf-8')

    # Header signature may contain "v1," prefix
    signatures = [s.replace("v1,", "").strip() for s in webhook_signature.split(" ")]
    return any(hmac.compare_digest(sig, expected_sig) for sig in signatures)

# --- HTTP CONTROL PLANE (AIOHTTP) ---
async def handle_register_node(request):
    if request.headers.get("X-Host-API-Key") != HOST_API_KEY:
        return web.json_response({"error": "Unauthorized key"}, status=401)
    
    try:
        data = await request.json()
        node_id = data.get("node_id")
        if not node_id or not isinstance(data.get("price_per_hour"), (int, float)):
            return web.json_response({"error": "Invalid payload format"}, status=400)
            
        db["nodes"][node_id] = {
            "gpu_model": str(data.get("gpu_model", "Unknown")),
            "vram": str(data.get("vram", "N/A")),
            "price_per_hour": float(data.get("price_per_hour")),
            "ssh_connection": str(data.get("ssh_connection")),
            "web_ui_url": str(data.get("web_ui_url", "")),
            "status": "available",
            "last_heartbeat": datetime.utcnow().isoformat()
        }
        save_db(db)
        return web.json_response({"status": "registered", "node_id": node_id})
    except Exception as e:
        return web.json_response({"error": "Server error"}, status=500)

async def handle_whop_webhook(request):
    raw_body = await request.read()
    headers = request.headers

    # 1. Enforce Webhook Signature
    if WHOP_WEBHOOK_SECRET and not verify_whop_signature(raw_body, headers, WHOP_WEBHOOK_SECRET):
        return web.json_response({"error": "Invalid webhook signature"}, status=401)

    try:
        payload = json.loads(raw_body)
        webhook_id = headers.get("webhook-id") or payload.get("id")

        # 2. Prevent Replay Attacks
        if webhook_id in db["processed_webhooks"]:
            return web.json_response({"status": "already_processed"}, status=200)

        event_type = payload.get("action") or payload.get("type")
        if event_type == "payment.succeeded":
            data = payload.get("data", {})
            discord_id = str(data.get("custom_fields", {}).get("discord_user_id", ""))
            amount = float(data.get("final_amount", 0)) / 100.0  # Amount in dollars

            if discord_id and amount > 0:
                current_bal = db["users"].get(discord_id, {}).get("balance", 0.0)
                db["users"][discord_id] = {"balance": round(current_bal + amount, 2)}
                
                # Mark as processed
                db["processed_webhooks"].append(webhook_id)
                # Keep audit log manageable
                if len(db["processed_webhooks"]) > 1000:
                    db["processed_webhooks"].pop(0)
                
                save_db(db)

        return web.json_response({"status": "success"}, status=200)
    except Exception as e:
        return web.json_response({"error": "Malformed payload"}, status=400)

# --- DISCORD BOT SETUP ---
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# --- BACKGROUND BILLING WORKER ---
@tasks.loop(seconds=30)
async def billing_loop():
    """Continuously deducts user balances for active rentals and auto-terminates when depleted."""
    async with rental_lock:
        now = datetime.utcnow()
        to_stop = []

        for rental_id, rental in list(db["rentals"].items()):
            user_id = rental["user_id"]
            node_id = rental["node_id"]
            price_per_min = rental["price_per_hour"] / 60.0
            
            # Deduct 30 seconds of compute time ($0.5 * min_rate)
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
            save_db(db)
            
            # Notify user on Discord
            try:
                user = await bot.fetch_user(int(user_id))
                if user:
                    await user.send(f"⚠️ **Rental Terminated**: Your GPU session on `{node_id}` was automatically stopped due to an insufficient balance.")
            except Exception:
                pass

        if db["rentals"] or to_stop:
            save_db(db)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    billing_loop.start()
    await bot.tree.sync()

# --- DISCORD COMMANDS ---
@bot.tree.command(name="rent_gpu", description="Rent an available GPU node")
async def rent_gpu(interaction: discord.Interaction, node_id: str):
    async with rental_lock:  # Prevent double-booking race condition
        user_id = str(interaction.user.id)
        balance = db["users"].get(user_id, {}).get("balance", 0.0)
        node = db["nodes"].get(node_id)

        if not node or node["status"] != "available":
            await interaction.response.send_message("❌ This GPU node is unavailable or does not exist.", ephemeral=True)
            return

        if balance < (node["price_per_hour"] / 60.0 * 5):  # Require at least 5 minutes of credit
            await interaction.response.send_message("❌ Insufficient balance! Please top up your account.", ephemeral=True)
            return

        # Assign rental
        node["status"] = "occupied"
        rental_id = f"{user_id}_{node_id}_{int(datetime.utcnow().timestamp())}"
        db["rentals"][rental_id] = {
            "user_id": user_id,
            "node_id": node_id,
            "price_per_hour": node["price_per_hour"],
            "start_time": datetime.utcnow().isoformat()
        }
        save_db(db)

        await interaction.response.send_message(
            f"✅ **Rental Started!**\n"
            f"**Node:** {node_id} ({node['gpu_model']})\n"
            f"**SSH:** `{node['ssh_connection']}`\n"
            f"**Web UI:** {node['web_ui_url'] or 'N/A'}",
            ephemeral=True
        )

# --- STARTUP SERVER & BOT ---
async def main():
    app = web.Application()
    app.router.add_post("/register-node", handle_register_node)
    app.router.add_post("/whop-webhook", handle_whop_webhook)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080)))
    await site.start()

    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
