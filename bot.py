from discord.ext import tasks
from threading import Thread
import os
import json
import asyncio
import aiohttp
from quart import Quart, request
import discord
from discord.ext import commands
from discord import app_commands

app = Quart(__name__)

# Absolute data directory path inside Railway containers
ROBLOX_LINKS_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "roblox_links.json"))

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

async def _exchange_roblox_code(code: str, redirect_uri: str) -> dict:
    client_id = os.getenv("ROBLOX_CLIENT_ID", "")
    client_secret = os.getenv("ROBLOX_CLIENT_SECRET", "")

    if not client_id or not client_secret or client_id.startswith("YOUR_"):
        print("⚠️ Warning: ROBLOX_CLIENT_ID or ROBLOX_CLIENT_SECRET is missing in Railway Variables!")
        return {}

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": "https://replit.app",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://roblox.com",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            try:
                return await r.json()
            except Exception:
                return {}

@app.route("/api/roblox/callback")
async def roblox_callback():
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")

    if error:
        return f"<h1>Roblox link failed</h1><p>{error}</p>", 400
    if not code or not state:
        return "<h1>Roblox link failed</h1><p>Missing authorization data.</p>", 400

    try:
        # GUARANTEED FIX: Splits the string and safely takes index 0 to get the string ID out of the list
        state_parts = state.split(":", 1)
        discord_id = int(state_parts[0])
    except (ValueError, IndexError, TypeError):
        return "<h1>Roblox link failed</h1><p>Invalid Discord user state.</p>", 400

    real_redirect = "https://replit.app"
    try:
        token_data = await _exchange_roblox_code(code, real_redirect)
    except Exception as e:
        print(f"❌ Error exchanging code: {e}")
        token_data = {}
    if not token_data or not token_data.get("access_token"):
        return "<h1>Roblox link failed</h1><p>The bot could not exchange the authorization code with Roblox.</p>", 400

    _store_roblox_link(discord_id, token_data)
class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.all())

    async def setup_hook(self):
        print("Background web engines linked")

bot = MyBot()

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        print("🔄 Syncing global application slash commands with Discord...")
def _load_roblox_link(discord_id: int) -> dict | None:
    links = _load_roblox_links()
    return links.get(str(discord_id))


async def _exchange_roblox_code(code: str, redirect_uri: str) -> dict:
    client_id = os.getenv("ROBLOX_CLIENT_ID", "")
    client_secret = os.getenv("ROBLOX_CLIENT_SECRET", "")

    if not client_id or not client_secret or client_id.startswith("YOUR_"):
        print("⚠️ Warning: ROBLOX_CLIENT_ID or ROBLOX_CLIENT_SECRET is missing in Railway Variables!")
        return {}

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": "https://replit.app",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://roblox.com",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            try:
                return await r.json()
            except Exception:
                return {}

def _save_roblox_links(data: dict) -> None:
    os.makedirs(os.path.dirname(ROBLOX_LINKS_FILE), exist_ok=True)
    with open(ROBLOX_LINKS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def _store_roblox_link(discord_id: int, token_data: dict) -> None:
    links = _load_roblox_links()
    # Force key to string so looking it up via Discord commands matches perfectly
    links[str(discord_id)] = token_data
    _save_roblox_links(links)

def _load_roblox_link(discord_id: int) -> dict | None:
    links = _load_roblox_links()
    return links.get(str(discord_id))

async def _exchange_roblox_code(code: str, redirect_uri: str) -> dict:
    # Safely fetch credentials from environment variables populated via Railway Variables dashboard
    client_id = os.getenv("ROBLOX_CLIENT_ID", "")
    client_secret = os.getenv("ROBLOX_CLIENT_SECRET", "")

    if not client_id or not client_secret or client_id.startswith("YOUR_"):
        print("⚠️ Warning: ROBLOX_CLIENT_ID or ROBLOX_CLIENT_SECRET is missing in Railway Variables!")
        return {}

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": "https://replit.app",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://roblox.com",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            try:
                return await r.json()
            except Exception:
                return {}


# Force an absolute data directory path that works flawlessly inside Railway containers
ROBLOX_LINKS_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "roblox_links.json"))

def _get_roblox_redirect_uri() -> str:
    # Forces your exact, valid production callback URL
    return "https://replit.app"

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
    # Force key to string so looking it up via Discord commands matches perfectly
    links[str(discord_id)] = token_data
    _save_roblox_links(links)

def _load_roblox_link(discord_id: int) -> dict | None:
    links = _load_roblox_links()
    return links.get(str(discord_id))

async def _exchange_roblox_code(code: str, redirect_uri: str) -> dict:
    # Safely fetch credentials from environment variables populated via Railway Variables dashboard
    client_id = os.getenv("ROBLOX_CLIENT_ID", "")
    client_secret = os.getenv("ROBLOX_CLIENT_SECRET", "")

    if not client_id or not client_secret or client_id.startswith("YOUR_"):
        print("⚠️ Warning: ROBLOX_CLIENT_ID or ROBLOX_CLIENT_SECRET is missing in Railway Variables!")
        return {}

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": "https://replit.app",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://roblox.com",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            try:
                return await r.json()
            except Exception:
                return {}

YOUTUBE_HANDLE       = "@JaxonRoblo"
YOUTUBE_LIVE_URL     = "https://www.youtube.com/@JaxonRoblo/live"
YOUTUBE_CHANNEL_PAGE = "https://www.youtube.com/@JaxonRoblo"
NOTIFY_SERVER_NAME   = "Meteor Run"          # partial match, case-insensitive
NOTIFY_CHANNEL_KEYWORDS = ["video", "upload"] # channel must contain both words

_yt_was_live      = False
_yt_was_scheduled = False
_scheduled_msg: discord.Message | None = None   # message to edit when live starts
_last_video_id    = None   # tracks newest upload from RSS
_yt_channel_id    = None   # resolved once at startup
_yt_live_title    = None   # title of current live stream

YT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

async def _resolve_yt_channel_id(session: aiohttp.ClientSession) -> str | None:
    """Fetch the UCxxxxx channel ID from the @handle page (needed for RSS).
    Tries several page variants and multiple regex patterns for resilience.
    """
    patterns = [
        r'"channelId"\s*:\s*"(UC[A-Za-z0-9_-]{22})"',
        r'"externalChannelId"\s*:\s*"(UC[A-Za-z0-9_-]{22})"',
        r'channel/(UC[A-Za-z0-9_-]{22})',
        r'"browseId"\s*:\s*"(UC[A-Za-z0-9_-]{22})"',
        r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[A-Za-z0-9_-]{22})"',
    ]
    urls_to_try = [YOUTUBE_CHANNEL_PAGE, YOUTUBE_LIVE_URL, f"{YOUTUBE_CHANNEL_PAGE}/about"]
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
    """Search bot guilds for the Meteor Run server + video uploads channel."""
    for guild in bot.guilds:
        if NOTIFY_SERVER_NAME.lower() not in guild.name.lower():
            continue
        for ch in guild.text_channels:
            name = ch.name.lower()
            if all(kw in name for kw in NOTIFY_CHANNEL_KEYWORDS):
                return ch
        # Fallback: any channel with 'video' OR 'upload'
        for ch in guild.text_channels:
            if any(kw in ch.name.lower() for kw in NOTIFY_CHANNEL_KEYWORDS):
                return ch
    return None

def _make_embed(*, kind: str, title: str, url: str) -> tuple[str, discord.Embed]:
    """Build the content string + embed for a given kind: scheduled | live | upload."""
    if kind == "scheduled":
        headline = "📅 Jaxon Has Made A **Scheduled Live** on YouTube!"
        colour   = discord.Color.orange()
        label    = "Set a Reminder 🔔"
    elif kind == "live":
        headline = "🔴 Jaxon Has Gone **LIVE** on YouTube!"
        colour   = discord.Color.red()
        label    = "Watch Now 🔴"
    else:  # upload
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
    """Every 60 s: detect live streams + new uploads, post to Meteor Run.

    State machine:
      nothing  →  scheduled  : post scheduled notification, store message ref
      scheduled → live       : edit stored message to live version, switch presence
      live      → nothing    : restore presence
      scheduled → nothing    : clear state (stream cancelled)
    """
    global _yt_was_live, _yt_was_scheduled, _scheduled_msg
    global _last_video_id, _yt_channel_id, _yt_live_title

    try:
        async with aiohttp.ClientSession(headers=YT_HEADERS) as session:

            # ── 1. Resolve channel ID once ────────────────────────────────────
            if not _yt_channel_id:
                _yt_channel_id = await _resolve_yt_channel_id(session)
                print(f"YouTube channel ID resolved: {_yt_channel_id}")

            # ── 2. Fetch live page & classify state ───────────────────────────
            # Primary signal: /@handle/live redirects to watch?v=... when live/scheduled.
            # Secondary: page content signals for live-vs-scheduled distinction.
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
            _live_content_signals = (
                'watching now' in live_page.lower()
                or '"isLiveNow":true' in live_page
                or '"liveBadge"' in live_page
                or 'live viewers' in live_page.lower()
                or '"isLive":true' in live_page
                or '"BADGE_STYLE_TYPE_LIVE_NOW"' in live_page
            )
            # Channel is live when: redirected to a video AND no upcoming marker AND
            # (content confirms live OR we already trust the redirect alone)
            is_scheduled = has_broadcast and is_upcoming
            is_live      = redirected_to_video and not is_upcoming

            # Debug output every cycle so you can see exactly what was detected
            print(
                f"[YT] final_url={final_url!r} redirected={redirected_to_video} "
                f"has_broadcast={has_broadcast} is_upcoming={is_upcoming} "
                f"content_signals={_live_content_signals} → "
                f"is_live={is_live} is_scheduled={is_scheduled} "
                f"was_live={_yt_was_live} was_sched={_yt_was_scheduled}"
            )

            # Helper: extract stream title from page / URL
            def _extract_title(page: str, fallback: str) -> str:
                for pat in [
                    r'"title"\s*:\s*\{"runs"\s*:\s*\[\{"text"\s*:\s*"([^"]+)"',
                    r'"title"\s*:\s*"([^"]{3,})"',
                    r'<title>([^<]+)</title>',
                ]:
                    m = re.search(pat, page)
                    if m:
                        return m.group(1).replace(" - YouTube", "").strip()
                return fallback

            # ── 3. State transitions ─────────────────────────────────────────

            # nothing → scheduled
            if is_scheduled and not _yt_was_scheduled and not _yt_was_live:
                _yt_was_scheduled = True
                title = _extract_title(live_page, "Upcoming Live Stream")
                _yt_live_title = title
                ch = _find_notify_channel()
                if ch:
                    content, embed = _make_embed(kind="scheduled", title=title, url=YOUTUBE_LIVE_URL)
                    _scheduled_msg = await ch.send(content, embed=embed, allowed_mentions=discord.AllowedMentions(everyone=True))
                print(f"YouTube: scheduled — '{title}'")

            # scheduled OR nothing → actually live
            elif is_live and not _yt_was_live:
                _yt_was_live      = True
                _yt_was_scheduled = False
                title = _extract_title(live_page, "Live Stream")
                _yt_live_title = title

                # Switch bot to streaming presence
                await bot.change_presence(
                    status=discord.Status.online,
                    activity=discord.Streaming(name=f"{YOUTUBE_HANDLE} is LIVE! 🔴", url=YOUTUBE_LIVE_URL),
                )

                # Edit existing scheduled msg → live, or send fresh if none
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

            # live → nothing (stream ended)
            elif _yt_was_live and not is_live and not is_scheduled:
                _yt_was_live   = False
                _yt_live_title = None
                _scheduled_msg = None
                await bot.change_presence(
                    activity=discord.Activity(type=discord.ActivityType.watching, name="over the server 👀")
                )
                print("YouTube: stream ended — presence restored.")

            # scheduled → nothing (cancelled before going live)
            elif _yt_was_scheduled and not is_scheduled and not is_live:
                _yt_was_scheduled = False
                _scheduled_msg    = None
                print("YouTube: scheduled stream was cancelled.")

            # ── 3. Check for new uploads via RSS ─────────────────────────────
            if _yt_channel_id:
                rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={_yt_channel_id}"
                async with session.get(rss_url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    rss_text = await r.text()

                root = ET.fromstring(rss_text)
                ns   = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
                entries = root.findall("atom:entry", ns)
                if entries:
                    latest = entries[0]
                    vid_id  = (latest.find("yt:videoId", ns) or type("", (), {"text": None})()).text
                    vid_title_el = latest.find("atom:title", ns)
                    vid_title = vid_title_el.text if vid_title_el is not None else "New Video"
                    vid_link_el  = latest.find("atom:link", ns)
                    vid_url   = vid_link_el.get("href") if vid_link_el is not None else f"https://youtu.be/{vid_id}"

                    if _last_video_id is None:
                        # First run — just record baseline, no notification
                        _last_video_id = vid_id
                    elif vid_id != _last_video_id and not is_live:
                        # New upload (skip if we're already live — same video)
                        _last_video_id = vid_id
                        ch = _find_notify_channel()
                        if ch:
                            await _send_yt_notification(ch, is_live=False, title=vid_title, url=vid_url)
                        print(f"YouTube: new upload — '{vid_title}'")

    except Exception as e:
        print(f"YouTube check error: {e}")

@check_youtube.before_loop
async def before_yt_check():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    print("⚡ FORCING INSTANT SERVER TREE SYNC...")
    try:
        for guild in bot.guilds:
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
        await bot.tree.sync()
        print("✅ SERVER RE-LINK COMPLETED SUCCESS!")
    except Exception as e:
        print(f"Sync: {e}")


async def main():
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
    if not DISCORD_TOKEN:
        print("❌ Error: DISCORD_TOKEN is missing in Railway Variables!")
        return

    # Check for Test Mode before binding loops
    if os.environ.get("BOT_TEST_MODE", "").lower() in {"1", "true", "yes", "on"}:
        print("Skipping Discord login in test mode.")
        return

    port = int(os.environ.get("PORT", 8080))
    print(f"📡 Web engine launching natively on Railway container port: {port}")

    from hypercorn.asyncio import serve
    from hypercorn.config import Config

    hypercorn_config = Config()
    hypercorn_config.bind = [f"0.0.0.0:{port}"]

    # Run the web server and the discord bot concurrently in the same loop
    await asyncio.gather(
        serve(app, hypercorn_config), 
        bot.start(DISCORD_TOKEN)      
    )

if __name__ == "__main__":
    asyncio.run(main())
