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

# ── Data files ────────────────────────────────────────────────────────────────
WELCOME_FILE   = "welcome_channels.json"
WARNINGS_FILE  = "warnings.json"
WARN_ROLES_FILE= "warn_roles.json"
STATS_FILE        = "bot_stats.json"
CUSTOM_CMDS_FILE  = "custom_commands.json"
AUTOMOD_FILE      = "automod_settings.json"

BOT_START_TIME = None

# Spam tracking: {guild_id: {user_id: [timestamps]}}
spam_tracker: dict = {}

def load_json(path):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

welcome_channels = load_json(WELCOME_FILE)
warnings_data    = load_json(WARNINGS_FILE)
warn_roles       = load_json(WARN_ROLES_FILE)

# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.all()

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        try:
            await self.tree.sync()
            print(f"✅ Synced slash commands for {self.user}")
        except Exception as e:
            print(f"❌ Error syncing commands: {e}")

bot = MyBot()

# ── Flask app for Roblox OAuth ────────────────────────────────────────────────
app = Flask(__name__)
ROBLOX_LINKS_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "roblox_links.json"))

ROBLOX_CLIENT_ID = os.getenv("ROBLOX_CLIENT_ID", "")
ROBLOX_CLIENT_SECRET = os.getenv("ROBLOX_CLIENT_SECRET", "")
ROBLOX_REDIRECT_URI = os.getenv("ROBLOX_REDIRECT_URI", "http://localhost:10000/api/roblox/callback")

def _load_roblox_links() -> dict:
    if not os.path.exists(ROBLOX_LINKS_FILE):
        return {}
    try:
        with open(ROBLOX_LINKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_roblox_links(data: dict) -> None:
    os.makedirs(os.path.dirname(ROBLOX_LINKS_FILE), exist_ok=True)
    with open(ROBLOX_LINKS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def _store_roblox_link(discord_id: int, token_data: dict) -> None:
    links = _load_roblox_links()
    links[str(discord_id)] = token_data
    _save_roblox_links(links)

def _load_roblox_link(discord_id: int) -> dict | None:
    links = _load_roblox_links()
    return links.get(str(discord_id))

async def _exchange_roblox_code(code: str, redirect_uri: str) -> dict:
    if ROBLOX_CLIENT_ID.startswith("YOUR_") or ROBLOX_CLIENT_SECRET.startswith("YOUR_"):
        return {}
    payload = {
        "client_id": ROBLOX_CLIENT_ID,
        "client_secret": ROBLOX_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://apis.roblox.com/oauth/v1/token",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            try:
                return await r.json()
            except Exception:
                return {}

@app.route("/api/roblox/callback")
def roblox_callback():
    try:
        code = request.args.get("code")
        state = request.args.get("state")
        error = request.args.get("error")
        if error:
            return f"<h1>Roblox link failed</h1><p>{error}</p>", 400
        if not code or not state:
            return "<h1>Roblox link failed</h1><p>Missing authorization data.</p>", 400
        try:
            discord_id = int(state)
        except ValueError:
            return "<h1>Roblox link failed</h1><p>Invalid Discord user state.</p>", 400

        token_data = asyncio.run(_exchange_roblox_code(code, ROBLOX_REDIRECT_URI))
        if not token_data.get("access_token"):
            return "<h1>Roblox link failed</h1><p>The bot could not exchange the authorization code with Roblox.</p>", 400

        _store_roblox_link(discord_id, token_data)
        return "<h1>✅ Roblox account linked</h1><p>You can now use the Roblox commands in Discord.</p>"
    except Exception as e:
        print(f"❌ Roblox callback error: {e}")
        return "<h1>Error</h1><p>An unexpected error occurred.</p>", 500

def _start_flask_server() -> None:
    try:
        port = int(os.environ.get("PORT", 10000))
        print(f"📡 Flask server starting on port {port}")
        app.run(host="0.0.0.0", port=port, debug=False)
    except Exception as e:
        print(f"❌ Flask error: {e}")

Thread(target=_start_flask_server, daemon=True).start()

@tasks.loop(seconds=30)
async def write_stats():
    try:
        stats = {
            "status": "online",
            "servers": len(bot.guilds),
            "users": sum(g.member_count or 0 for g in bot.guilds),
            "latency_ms": round(bot.latency * 1000),
            "uptime_seconds": round((datetime.datetime.utcnow() - BOT_START_TIME).total_seconds()) if BOT_START_TIME else 0,
            "command_count": 61,
            "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
        }
        save_json(STATS_FILE, stats)
    except Exception as e:
        print(f"❌ Stats write error: {e}")

# ── YouTube notification config ───────────────────────────────────────────────
YOUTUBE_HANDLE       = "@JaxonRoblo"
YOUTUBE_LIVE_URL     = "https://www.youtube.com/@JaxonRoblo/live"
YOUTUBE_CHANNEL_PAGE = "https://www.youtube.com/@JaxonRoblo"
NOTIFY_SERVER_NAME   = "Meteor Run"
NOTIFY_CHANNEL_KEYWORDS = ["video", "upload"]

_yt_was_live      = False
_yt_was_scheduled = False
_scheduled_msg: discord.Message | None = None
_last_video_id    = None
_yt_channel_id    = None
_yt_live_title    = None

YT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

async def _resolve_yt_channel_id(session: aiohttp.ClientSession) -> str | None:
    patterns = [
        r'"channelId"\s*:\s*"(UC[A-Za-z0-9_-]{22})"',
        r'"externalChannelId"\s*:\s*"(UC[A-Za-z0-9_-]{22})"',
        r'channel/(UC[A-Za-z0-9_-]{22})',
    ]
    urls_to_try = [YOUTUBE_CHANNEL_PAGE, YOUTUBE_LIVE_URL]
    for url in urls_to_try:
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
    for guild in bot.guilds:
        if NOTIFY_SERVER_NAME.lower() not in guild.name.lower():
            continue
        for ch in guild.text_channels:
            name = ch.name.lower()
            if all(kw in name for kw in NOTIFY_CHANNEL_KEYWORDS):
                return ch
        for ch in guild.text_channels:
            if any(kw in ch.name.lower() for kw in NOTIFY_CHANNEL_KEYWORDS):
                return ch
    return None

def _make_embed(*, kind: str, title: str, url: str) -> tuple[str, discord.Embed]:
    if kind == "scheduled":
        headline = "📅 Jaxon Has Made A **Scheduled Live** on YouTube!"
        colour   = discord.Color.orange()
        label    = "Set a Reminder 🔔"
    elif kind == "live":
        headline = "🔴 Jaxon Has Gone **LIVE** on YouTube!"
        colour   = discord.Color.red()
        label    = "Watch Now 🔴"
    else:
        headline = "📹 Jaxon Just Posted a New Video!"
        colour   = discord.Color.purple()
        label    = "Watch Now ▶️"

    embed = discord.Embed(
        title=title,
        url=url,
        description=f"[{label}]({url})",
        color=colour,
    )
    embed.set_author(name="JaxonRoblo", url=YOUTUBE_CHANNEL_PAGE)
    embed.set_footer(text="YouTube • " + datetime.datetime.utcnow().strftime("%b %d, %Y"))
    return f"@everyone {headline}", embed

@tasks.loop(seconds=60)
async def check_youtube():
    global _yt_was_live, _yt_was_scheduled, _scheduled_msg
    global _last_video_id, _yt_channel_id, _yt_live_title

    try:
        async with aiohttp.ClientSession(headers=YT_HEADERS) as session:
            if not _yt_channel_id:
                _yt_channel_id = await _resolve_yt_channel_id(session)
                print(f"YouTube channel ID resolved: {_yt_channel_id}")

            final_url = YOUTUBE_LIVE_URL
            live_page = ""
            async with session.get(
                YOUTUBE_LIVE_URL,
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=12),
            ) as r:
                final_url = str(r.url)
                live_page = await r.text()

            redirected_to_video = "watch?v=" in final_url
            has_broadcast       = '"isLiveBroadcast"' in live_page or redirected_to_video
            is_upcoming         = '"upcomingEventData"' in live_page
            is_scheduled = has_broadcast and is_upcoming
            is_live      = redirected_to_video and not is_upcoming

            if is_scheduled and not _yt_was_scheduled and not _yt_was_live:
                _yt_was_scheduled = True
                title = "Upcoming Live Stream"
                _yt_live_title = title
                ch = _find_notify_channel()
                if ch:
                    content, embed = _make_embed(kind="scheduled", title=title, url=YOUTUBE_LIVE_URL)
                    _scheduled_msg = await ch.send(content, embed=embed, allowed_mentions=discord.AllowedMentions(everyone=True))
                print(f"YouTube: scheduled — '{title}'")

            elif is_live and not _yt_was_live:
                _yt_was_live      = True
                _yt_was_scheduled = False
                title = "Live Stream"
                _yt_live_title = title

                await bot.change_presence(
                    status=discord.Status.online,
                    activity=discord.Streaming(name=f"{YOUTUBE_HANDLE} is LIVE! 🔴", url=YOUTUBE_LIVE_URL),
                )

                content, embed = _make_embed(kind="live", title=title, url=YOUTUBE_LIVE_URL)
                if _scheduled_msg:
                    try:
                        await _scheduled_msg.edit(content=content, embed=embed)
                        _scheduled_msg = None
                    except Exception:
                        ch = _find_notify_channel()
                        if ch:
                            await ch.send(content, embed=embed, allowed_mentions=discord.AllowedMentions(everyone=True))
                else:
                    ch = _find_notify_channel()
                    if ch:
                        await ch.send(content, embed=embed, allowed_mentions=discord.AllowedMentions(everyone=True))
                print(f"YouTube: LIVE — '{title}'")

            elif _yt_was_live and not is_live and not is_scheduled:
                _yt_was_live   = False
                _yt_live_title = None
                _scheduled_msg = None
                await bot.change_presence(
                    activity=discord.Activity(type=discord.ActivityType.watching, name="over the server 👀")
                )
                print("YouTube: stream ended — presence restored.")

            elif _yt_was_scheduled and not is_scheduled and not is_live:
                _yt_was_scheduled = False
                _scheduled_msg    = None
                print("YouTube: scheduled stream was cancelled.")

    except Exception as e:
        print(f"❌ YouTube check error: {e}")

@check_youtube.before_loop
async def before_yt_check():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    global BOT_START_TIME
    BOT_START_TIME = datetime.datetime.utcnow()
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name="over the server 👀")
    )
    print(f"✅ Logged in as {bot.user.name}")
    write_stats.start()
    check_youtube.start()

# ── Owner check ───────────────────────────────────────────────────────────────
BOT_ADMIN_ID = 1481589775507918942

def is_owner(interaction: discord.Interaction) -> bool:
    if interaction.user.id == BOT_ADMIN_ID:
        return True
    if interaction.guild and interaction.guild.owner:
        return interaction.user == interaction.guild.owner
    return False

# ── Simple test command ───────────────────────────────────────────────────────
@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    try:
        latency = round(bot.latency * 1000)
        await interaction.response.send_message(f"🏓 Pong! Latency: {latency}ms")
    except Exception as e:
        print(f"❌ Ping command error: {e}")
        await interaction.response.send_message("❌ Error executing command", ephemeral=True)

# ── Run bot ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN", "")
    if not token:
        print("❌ DISCORD_TOKEN not set!")
        exit(1)
    
    try:
        bot.run(token)
    except Exception as e:
        print(f"❌ Bot error: {e}")

