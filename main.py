import os
import time
import sqlite3
import hashlib
import threading
import requests
from flask import Flask, render_template, request, jsonify, redirect
import discord
from discord.ext import commands
from discord.ui import Button, View
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("ACCOUNT_CHANNEL_ID", 0))
GUILD_ID = int(os.getenv("GUILD_ID", 0))
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
VERIFIED_ROLE_ID = int(os.getenv("VERIFIED_ROLE_ID", 0))

DB_PATH = "/data/bazax_users.db" if os.path.exists("/data") else "bazax_users.db"

app = Flask(__name__)

@app.route('/')
def index():
    try: return render_template('index.html')
    except: return "BazaX API is alive", 200

@app.route('/dashboard')
def dashboard():
    try: return render_template('dashboard.html')
    except: return "Dashboard not found", 404

@app.route('/logout')
def logout(): return redirect('/')

@app.route('/login')
def login():
    url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds.join"
    return redirect(url)

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code: return "Ошибка: Код не получен", 400

    data = {
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI
    }
    r = requests.post("https://discord.com/api/oauth2/token", data=data)
    if r.status_code != 200: return f"Ошибка токена: {r.text}", 400
    
    token_data = r.json()
    access_token = token_data['access_token']
    headers = {"Authorization": f"Bearer {access_token}"}
    user_resp = requests.get("https://discord.com/api/users/@me", headers=headers)
    user_data = user_resp.json()
    user_id = user_data['id']
    username = user_data['username']
    avatar_hash = user_data.get('avatar')

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT plan, key FROM users WHERE discord_id = ?", (user_id,))
    row = c.fetchone()

    if row:
        plan, key = row
    else:
        plan = "FREE"
        # ЖЕСТКО ЗАДАЕМ КЛЮЧ BAZABYSTIT
        key = "bazabystit"
        c.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?)", (user_id, plan, key, 5, ""))
        c.execute("INSERT INTO key_history VALUES (?, ?, ?)", (key, user_id, int(time.time())))
        conn.commit()
    conn.close()

    if GUILD_ID and VERIFIED_ROLE_ID:
        bot_headers = {"Authorization": f"Bot {TOKEN}", "Content-Type": "application/json"}
        requests.put(f"https://discord.com/api/guilds/{GUILD_ID}/members/{user_id}/roles/{VERIFIED_ROLE_ID}", headers=bot_headers)

    avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png" if avatar_hash else "https://cdn.discordapp.com/embed/avatars/0.png"

    return f"""
    <html><head><meta charset="UTF-8"></head>
    <body style="background: #09090b; color: white; text-align: center; padding-top: 50px; font-family: sans-serif;">
        <h2 style="color: #e6192a;">Авторизация успешна!</h2>
        <p>Перенаправляем...</p>
        <script>
            localStorage.setItem('bazax_id', "{user_id}");
            localStorage.setItem('bazax_user', JSON.stringify({{
                username: "{username}", avatar: "{avatar_url}", key: "{key}", plan: "{plan}"
            }}));
            window.location.href = "/dashboard";
        </script>
    </body></html>
    """

# НОВЫЙ API ДЛЯ ДАШБОРДА (Отдает реальные данные из базы)
@app.route('/api/dashboard_data', methods=['GET'])
def dashboard_data():
    discord_id = request.args.get('id')
    if not discord_id: return jsonify({"error": "No ID"}), 400
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT plan, key, cloud_limit, devices FROM users WHERE discord_id = ?", (discord_id,))
    row = c.fetchone()
    conn.close()
    
    if not row: return jsonify({"error": "User not found"}), 404
    
    plan, key, cloud_limit, devices = row
    return jsonify({
        "plan": plan, "key": key, "cloud_limit": cloud_limit,
        "devices": devices if devices else "Нет привязанных устройств",
        "used_cloud": 0 # Пока 0
    })

# API ДЛЯ ИНЖЕКТОРА
@app.route('/api/get_info', methods=['GET'])
def get_info():
    key = request.args.get('key')
    hwid = request.args.get('hwid', 'Unknown_Device')
    if not key: return jsonify({"status": "error", "message": "Key is required"}), 400

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT discord_id, plan, cloud_limit, devices FROM users WHERE key = ?", (key,))
    row = c.fetchone()
    
    if not row:
        conn.close()
        return jsonify({"status": "error", "message": "Invalid key"}), 401

    discord_id, plan, cloud_limit, devices = row
    if devices is None: devices = hwid
    elif hwid not in devices: devices += f", {hwid}"
    c.execute("UPDATE users SET devices = ? WHERE key = ?", (devices, key))
    conn.commit()
    conn.close()

    features = {"internal": True, "beta_test": plan == "BOOST", "all_functions": plan == "BOOST"}
    return jsonify({"status": "success", "plan": plan, "cloud_limit_mb": cloud_limit, "features": features})

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (discord_id TEXT PRIMARY KEY, plan TEXT, key TEXT UNIQUE, cloud_limit INTEGER, devices TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS deleted_users (discord_id TEXT PRIMARY KEY, delete_time INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS key_history (key TEXT PRIMARY KEY, discord_id TEXT, created_at INTEGER)''')
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT plan, key, cloud_limit, devices FROM users WHERE discord_id = ?", (str(user_id),))
    row = c.fetchone()
    conn.close()
    return row

def delete_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE discord_id = ?", (str(user_id),))
    c.execute("INSERT OR REPLACE INTO deleted_users VALUES (?, ?)", (str(user_id), int(time.time())))
    conn.commit()
    conn.close()

init_db()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

class AccountView(View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="Login / Create", style=discord.ButtonStyle.success, custom_id="btn_login")
    async def btn_login(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("🔗 **Авторизация**\nhttps://bazax.onrender.com/login", ephemeral=True)

    @discord.ui.button(label="Your Plan", style=discord.ButtonStyle.secondary, custom_id="btn_plan")
    async def btn_plan(self, interaction: discord.Interaction, button: Button):
        user = get_user(interaction.user.id)
        if not user:
            await interaction.response.send_message("❌ Сначала авторизуйся.", ephemeral=True)
            return
        plan = user[0]
        desc = "5 MB Cloud\nInternal\n❌ Beta" if plan == "FREE" else "100 MB Cloud\nInternal\n✅ Beta"
        embed = discord.Embed(title=f"💎 Plan: {plan}", description=desc, color=0x5865F2)
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.event
async def on_ready():
    print(f"[+] Бот {bot.user} запущен!")
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        async for message in channel.history(limit=10):
            if message.author == bot.user: await message.delete()
        embed = discord.Embed(title="**ACCOUNT**", description="Your system is already open here! Where do we start?", color=0x2b2d31)
        await channel.send(embed=embed, view=AccountView())

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    bot.run(TOKEN)
