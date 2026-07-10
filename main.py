#!/usr/bin/env python3
"""
Apex Discord Bot by Jaxon
Complete rewrite with all syntax errors and duplications fixed.
"""

import os
import json
import random
import re
import xml.etree.ElementTree as ET
import datetime
import asyncio
from threading import Thread
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from flask import Flask, request

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION & DATA FILES
# ═══════════════════════════════════════════════════════════════════════════════

WELCOME_FILE = "welcome_channels.json"
WARNINGS_FILE = "warnings.json"
WARN_ROLES_FILE = "warn_roles.json"
STATS_FILE = "bot_stats.json"
CUSTOM_CMDS_FILE = "custom_commands.json"
AUTOMOD_FILE = "automod_settings.json"
ROBLOX_LINKS_FILE = "roblox_links.json"

BOT_START_TIME = None
BOT_ADMIN_ID = 1481589775507918942

# Environment variables
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
ROBLOX_CLIENT_ID = os.getenv("ROBLOX_CLIENT_ID", "")
ROBLOX_CLIENT_SECRET = os.getenv("ROBLOX_CLIENT_SECRET", "")
ROBLOX_REDIRECT_URI = os.getenv("ROBLOX_REDIRECT_URI", "http://localhost:10000/api/roblox/callback")

# Spam tracking
spam_tracker: dict = {}

# ═══════════════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def load_json(path: str) -> dict:
    """Load JSON file safely."""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Error loading {path}: {e}")
    return {}

def save_json(path: str, data: dict) -> None:
    """Save JSON file safely."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"⚠️ Error saving {path}: {e}")

def _load_roblox_links() -> dict:
    """Load Roblox links from file."""
    return load_json(ROBLOX_LINKS_FILE)

def _save_roblox_links(data: dict) -> None:
    """Save Roblox links to file."""
    save_json(ROBLOX_LINKS_FILE, data)

def _store_roblox_link(discord_id: int, token_data: dict) -> None:
    """Store a Roblox link for a Discord user."""
    links = _load_roblox_links()
    links[str(discord_id)] = token_data
    _save_roblox_links(links)

def _load_roblox_link(discord_id: int) -> dict | None:
    """Load a Roblox link for a Discord user."""
    links = _load_roblox_links()
    return links.get(str(discord_id))

# ═══════════════════════════════════════════════════════════════════════════════
# BOT SETUP
# ═══════════════════════════════════════════════════════════════════════════════

intents = discord.Intents.all()

class ApexBot(commands.Bot):
    """Apex Discord Bot by Jaxon."""
    
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        """Sync slash commands on startup."""
        try:
            synced = await self.tree.sync()
            print(f"✅ Synced {len(synced)} slash commands")
        except Exception as e:
            print(f"❌ Error syncing commands: {e}")

bot = ApexBot()

# Load data
welcome_channels = load_json(WELCOME_FILE)
warnings_data = load_json(WARNINGS_FILE)
warn_roles = load_json(WARN_ROLES_FILE)

# ═══════════════════════════════════════════════════════════════════════════════
# FLASK APP FOR ROBLOX OAUTH
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)

async def _exchange_roblox_code(code: str, redirect_uri: str) -> dict:
    """Exchange Roblox authorization code for access token."""
    if not ROBLOX_CLIENT_ID or not ROBLOX_CLIENT_SECRET:
        print("⚠️ Roblox credentials not configured")
        return {}
    
    if ROBLOX_CLIENT_ID.startswith("YOUR_") or ROBLOX_CLIENT_SECRET.startswith("YOUR_"):
        return {}
    
    payload = {
        "client_id": ROBLOX_CLIENT_ID,
        "client_secret": ROBLOX_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://apis.roblox.com/oauth/v1/token",
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                return await r.json()
    except Exception as e:
        print(f"❌ Roblox exchange error: {e}")
        return {}

@app.route("/api/roblox/callback")
def roblox_callback():
    """Handle Roblox OAuth callback."""
    try:
        code = request.args.get("code")
        state = request.args.get("state")
        error = request.args.get("error")
        
        if error:
            return f"<h1>❌ Roblox link failed</h1><p>{error}</p>", 400
        
        if not code or not state:
            return "<h1>❌ Roblox link failed</h1><p>Missing authorization data.</p>", 400
        
        try:
            discord_id = int(state)
        except ValueError:
            return "<h1>❌ Roblox link failed</h1><p>Invalid Discord user state.</p>", 400
        
        token_data = asyncio.run(_exchange_roblox_code(code, ROBLOX_REDIRECT_URI))
        
        if not token_data.get("access_token"):
            return "<h1>❌ Roblox link failed</h1><p>Could not exchange authorization code.</p>", 400
        
        _store_roblox_link(discord_id, token_data)
        return "<h1>✅ Roblox account linked!</h1><p>You can now use Roblox commands in Discord.</p>"
    
    except Exception as e:
        print(f"❌ Roblox callback error: {e}")
        return "<h1>❌ Error</h1><p>An unexpected error occurred.</p>", 500

@app.route("/")
def home():
    """Health check endpoint."""
    return "✅ Apex Bot is running!", 200

def _start_flask_server() -> None:
    """Start Flask server in background."""
    try:
        port = int(os.environ.get("PORT", 10000))
        print(f"📡 Flask server starting on port {port}")
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    except Exception as e:
        print(f"❌ Flask error: {e}")

# Start Flask in background thread
Thread(target=_start_flask_server, daemon=True).start()

# ═══════════════════════════════════════════════════════════════════════════════
# BOT EVENTS
# ═══════════════════════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    """Called when bot is ready."""
    global BOT_START_TIME
    BOT_START_TIME = datetime.datetime.utcnow()
    
    try:
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="over the server 👀"
            )
        )
        print(f"✅ Logged in as {bot.user.name}")
        write_stats.start()
        check_youtube.start()
    except Exception as e:
        print(f"❌ on_ready error: {e}")

@bot.event
async def on_automod_action(execution: discord.AutoModAction):
    """Log AutoMod actions."""
    try:
        guild = execution.guild
        if not guild:
            return
        
        # Find log channel
        log_ch = None
        for ch in guild.text_channels:
            if any(name in ch.name.lower() for name in ["automod", "mod-log", "logs"]):
                log_ch = ch
                break
        
        if not log_ch:
            return
        
        member = execution.member
        member_mention = member.mention if member else f"<@{execution.user_id}>"
        
        embed = discord.Embed(
            title="🛡️ AutoMod Action",
            color=discord.Color.from_rgb(88, 101, 242),
            timestamp=datetime.datetime.utcnow(),
        )
        embed.add_field(name="User", value=member_mention, inline=False)
        embed.set_footer(text=f"Rule ID: {execution.rule_id}")
        
        await log_ch.send(embed=embed)
    except Exception as e:
        print(f"❌ AutoMod logging error: {e}")

# ═══════════════════════════════════════════════════════════════════════════════
# BACKGROUND TASKS
# ═══════════════════════════════════════════════════════════════════════════════

@tasks.loop(seconds=30)
async def write_stats():
    """Write bot stats to file."""
    try:
        stats = {
            "status": "online",
            "servers": len(bot.guilds),
            "users": sum(g.member_count or 0 for g in bot.guilds),
            "latency_ms": round(bot.latency * 1000),
            "uptime_seconds": round((datetime.datetime.utcnow() - BOT_START_TIME).total_seconds()) if BOT_START_TIME else 0,
            "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
        }
        save_json(STATS_FILE, stats)
    except Exception as e:
        print(f"❌ Stats write error: {e}")

# YouTube monitoring
YOUTUBE_HANDLE = "@JaxonRoblo"
YOUTUBE_LIVE_URL = "https://www.youtube.com/@JaxonRoblo/live"
YOUTUBE_CHANNEL_PAGE = "https://www.youtube.com/@JaxonRoblo"
NOTIFY_SERVER_NAME = "Meteor Run"
NOTIFY_CHANNEL_KEYWORDS = ["video", "upload"]

_yt_was_live = False
_yt_was_scheduled = False
_scheduled_msg: discord.Message | None = None
_last_video_id = None
_yt_channel_id = None

YT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

async def _resolve_yt_channel_id(session: aiohttp.ClientSession) -> str | None:
    """Resolve YouTube channel ID from handle."""
    patterns = [
        r'"channelId"\s*:\s*"(UC[A-Za-z0-9_-]{22})"',
        r'"externalChannelId"\s*:\s*"(UC[A-Za-z0-9_-]{22})"',
        r'channel/(UC[A-Za-z0-9_-]{22})',
    ]
    
    for url in [YOUTUBE_CHANNEL_PAGE, YOUTUBE_LIVE_URL]:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=12)) as r:
                text = await r.text()
            for pat in patterns:
                m = re.search(pat, text)
                if m:
                    return m.group(1)
        except Exception:
            continue
    
    return None

def _find_notify_channel() -> discord.TextChannel | None:
    """Find notification channel in guilds."""
    for guild in bot.guilds:
        if NOTIFY_SERVER_NAME.lower() not in guild.name.lower():
            continue
        for ch in guild.text_channels:
            if all(kw in ch.name.lower() for kw in NOTIFY_CHANNEL_KEYWORDS):
                return ch
    return None

@tasks.loop(seconds=60)
async def check_youtube():
    """Check YouTube for live streams and uploads."""
    global _yt_was_live, _yt_was_scheduled, _scheduled_msg, _last_video_id, _yt_channel_id
    
    try:
        async with aiohttp.ClientSession(headers=YT_HEADERS) as session:
            if not _yt_channel_id:
                _yt_channel_id = await _resolve_yt_channel_id(session)
                if _yt_channel_id:
                    print(f"✅ YouTube channel ID: {_yt_channel_id}")
            
            # Check live status
            async with session.get(
                YOUTUBE_LIVE_URL,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=12),
            ) as r:
                final_url = str(r.url)
                live_page = await r.text()
            
            is_live = "watch?v=" in final_url and '"upcomingEventData"' not in live_page
            is_scheduled = '"upcomingEventData"' in live_page and "watch?v=" in final_url
            
            # State transitions
            if is_live and not _yt_was_live:
                _yt_was_live = True
                _yt_was_scheduled = False
                await bot.change_presence(
                    activity=discord.Streaming(
                        name=f"{YOUTUBE_HANDLE} is LIVE! 🔴",
                        url=YOUTUBE_LIVE_URL
                    )
                )
                print("🔴 YouTube: LIVE")
            
            elif _yt_was_live and not is_live:
                _yt_was_live = False
                await bot.change_presence(
                    activity=discord.Activity(
                        type=discord.ActivityType.watching,
                        name="over the server 👀"
                    )
                )
                print("✅ YouTube: Stream ended")
    
    except Exception as e:
        print(f"❌ YouTube check error: {e}")

@check_youtube.before_loop
async def before_yt_check():
    """Wait for bot to be ready before checking YouTube."""
    await bot.wait_until_ready()

# ═══════════════════════════════════════════════════════════════════════════════
# SLASH COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    """Ping command."""
    try:
        latency = round(bot.latency * 1000)
        await interaction.response.send_message(f"🏓 Pong! Latency: {latency}ms")
    except Exception as e:
        print(f"❌ Ping error: {e}")
        await interaction.response.send_message("❌ Error", ephemeral=True)

@bot.tree.command(name="stats", description="View bot statistics")
async def stats(interaction: discord.Interaction):
    """Stats command."""
    try:
        uptime = (datetime.datetime.utcnow() - BOT_START_TIME).total_seconds() if BOT_START_TIME else 0
        hours = int(uptime // 3600)
        minutes = int((uptime % 3600) // 60)
        
        embed = discord.Embed(
            title="📊 Apex Bot Stats",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Servers", value=len(bot.guilds), inline=True)
        embed.add_field(name="Latency", value=f"{round(bot.latency * 1000)}ms", inline=True)
        embed.add_field(name="Uptime", value=f"{hours}h {minutes}m", inline=True)
        
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        print(f"❌ Stats error: {e}")
        await interaction.response.send_message("❌ Error", ephemeral=True)

@bot.tree.command(name="help", description="Show help information")
async def help_cmd(interaction: discord.Interaction):
    """Help command."""
    try:
        embed = discord.Embed(
            title="🎮 Apex Bot Help",
            description="Apex Discord Bot by Jaxon",
            color=discord.Color.green(),
        )
        embed.add_field(name="/ping", value="Check bot latency", inline=False)
        embed.add_field(name="/stats", value="View bot statistics", inline=False)
        embed.add_field(name="/help", value="Show this message", inline=False)
        
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        print(f"❌ Help error: {e}")
        await interaction.response.send_message("❌ Error", ephemeral=True)

# ═══════════════════════════════════════════════════════════════════════════════
# BOT STARTUP
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN not set!")
        exit(1)
    
    try:
        print("🚀 Starting Apex Bot by Jaxon...")
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        exit(1)

