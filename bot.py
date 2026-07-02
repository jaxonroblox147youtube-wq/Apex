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
from dotenv import load_dotenv
from discord import app_commands
from discord.ext import commands, tasks
from flask import Flask, request

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

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

bot = MyBot()

app = Flask(__name__)
ROBLOX_LINKS_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "roblox_links.json"))


def _get_roblox_redirect_uri() -> str:
    configured = os.getenv("ROBLOX_REDIRECT_URI", "").strip()
    if configured:
        return configured

    for env_name in ("REPLIT_URL", "PUBLIC_URL", "APP_URL", "URL"):
        value = os.getenv(env_name, "").strip()
        if value:
            return value.rstrip("/") + "/api/roblox/callback"

    for env_name in ("REPL_SLUG",):
        value = os.getenv(env_name, "").strip()
        if value:
            return f"https://{value}.replit.app/api/roblox/callback"

    return "http://localhost:10000/api/roblox/callback"


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
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")
    if error:
        return f"<h1>Roblox link failed</h1><p>{error}</p>", 400
    if not code or not state:
        return "<h1>Roblox link failed</h1><p>Missing authorization data.</p>", 400
    try:
        discord_id_raw = state.split(":", 1)[0]
        discord_id = int(discord_id_raw)
    except ValueError:
        return "<h1>Roblox link failed</h1><p>Invalid Discord user state.</p>", 400

    token_data = asyncio.run(_exchange_roblox_code(code, ROBLOX_REDIRECT_URI))
    if not token_data.get("access_token"):
        return "<h1>Roblox link failed</h1><p>The bot could not exchange the authorization code with Roblox.</p>", 400

    _store_roblox_link(discord_id, token_data)
    return "<h1>✅ Roblox account linked</h1><p>You can now use the Roblox commands in Discord.</p>"


def _start_flask_server() -> None:
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))


Thread(target=_start_flask_server, daemon=True).start()

@tasks.loop(seconds=30)
async def write_stats():
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

# ── YouTube notification config ───────────────────────────────────────────────
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
    global BOT_START_TIME
    BOT_START_TIME = datetime.datetime.utcnow()
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.watching, name="over the server 👀")
    )
    print(f"Logged in as {bot.user.name}")
    try:
        print("Starting slash command sync...")
        synced = await bot.tree.sync()
        print(f"Success! Synced {len(synced)} slash commands globally.")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")
    write_stats.start()
    check_youtube.start()

# ── AutoMod log channel finder ────────────────────────────────────────────────
_AUTOMOD_LOG_NAMES = ("automod-log", "automod-logs", "mod-log", "mod-logs",
                      "modlog", "modlogs", "audit-log", "audit-logs",
                      "logs", "bot-logs", "server-logs")

def _find_automod_log_channel(guild: discord.Guild):
    """Return the best text channel for AutoMod logs, or None."""
    for name in _AUTOMOD_LOG_NAMES:
        ch = discord.utils.get(guild.text_channels, name=name)
        if ch:
            return ch
    # Fallback: any channel whose name contains "log" or "mod"
    for ch in guild.text_channels:
        if "log" in ch.name or "automod" in ch.name:
            return ch
    return None

# ── AutoMod action logger ─────────────────────────────────────────────────────
AUTOMOD_TRIGGER_LABELS = {
    discord.AutoModRuleTriggerType.keyword:          "🔤 Blocked Keyword",
    discord.AutoModRuleTriggerType.spam:             "📨 Spam Detected",
    discord.AutoModRuleTriggerType.keyword_preset:   "📋 Preset Keyword",
    discord.AutoModRuleTriggerType.mention_spam:     "🔔 Mention Spam",
    discord.AutoModRuleTriggerType.harmful_link:     "🔗 Harmful Link",
}

AUTOMOD_ACTION_LABELS = {
    discord.AutoModRuleActionType.block_message:         "🚫 Message Blocked",
    discord.AutoModRuleActionType.send_alert_message:    "📢 Alert Sent",
    discord.AutoModRuleActionType.timeout:               "⏱️ User Timed Out",
}

@bot.event
async def on_automod_action(execution: discord.AutoModAction):
    guild = execution.guild
    log_ch = _find_automod_log_channel(guild)
    if not log_ch:
        return

    member        = execution.member
    member_mention = member.mention if member else f"<@{execution.user_id}>"
    member_name    = str(member)    if member else str(execution.user_id)
    avatar_url     = member.display_avatar.url if member else None

    trigger_label = AUTOMOD_TRIGGER_LABELS.get(execution.rule_trigger_type, "⚠️ AutoMod Rule")
    action_label  = AUTOMOD_ACTION_LABELS.get(execution.action.type, "🤖 Action Taken")

    embed = discord.Embed(
        title="🛡️ AutoMod Action",
        color=discord.Color.from_rgb(88, 101, 242),   # Discord blurple — matches AutoMod UI
        timestamp=datetime.datetime.utcnow(),
    )
    embed.add_field(name="User",    value=f"{member_mention} (`{member_name}`)", inline=False)
    embed.add_field(name="Trigger", value=trigger_label,  inline=True)
    embed.add_field(name="Action",  value=action_label,   inline=True)

    if execution.channel_id:
        embed.add_field(name="Channel", value=f"<#{execution.channel_id}>", inline=True)

    if execution.matched_keyword:
        embed.add_field(name="Matched Keyword",
                        value=f"`{execution.matched_keyword}`", inline=True)

    if execution.matched_content:
        snippet = execution.matched_content[:300]
        embed.add_field(name="Matched Content",
                        value=f"||{snippet}||", inline=False)
    elif execution.content:
        snippet = execution.content[:300]
        embed.add_field(name="Message Content",
                        value=f"||{snippet}||", inline=False)

    if execution.action.type == discord.AutoModRuleActionType.timeout and execution.action.duration:
        dur = int(execution.action.duration.total_seconds())
        embed.add_field(name="Timeout Duration",
                        value=f"{dur}s ({dur // 60}m)", inline=True)

    embed.set_footer(text=f"Rule ID: {execution.rule_id} • AutoMod")
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)

    try:
        await log_ch.send(embed=embed)
    except discord.Forbidden:
        pass

# ── Owner check ───────────────────────────────────────────────────────────────
BOT_ADMIN_ID = 1481589775507918942

def is_owner(interaction: discord.Interaction) -> bool:
    if interaction.user.id == BOT_ADMIN_ID:
        return True
    if interaction.guild and interaction.guild.owner:
        return interaction.user == interaction.guild.owner
    return False

# ── /deploy-rules — create 6 keyword AutoMod rules for the badge ─────────────
_DEPLOY_RULES = [
    {
        "name": "🚫 Hate Speech Filter",
        "keywords": [
            "nigger", "nigga", "faggot", "fag", "chink", "spic", "kike",
            "wetback", "beaner", "towelhead", "raghead", "cracker", "honky",
            "tranny", "dyke", "retard", "retarded",
        ],
    },
    {
        "name": "🤬 Profanity Filter",
        "keywords": [
            "fuck", "fucker", "fucking", "motherfucker", "shit", "shithead",
            "asshole", "bitch", "bastard", "cunt", "cock", "dick", "pussy",
            "whore", "slut", "dickhead", "dumbass", "jackass",
        ],
    },
    {
        "name": "🔞 NSFW Content Filter",
        "keywords": [
            "porn", "pornhub", "xvideos", "xnxx", "onlyfans", "nude", "nudes",
            "nsfw", "hentai", "milf", "blowjob", "handjob", "cumshot",
            "sex tape", "leaked nudes",
        ],
    },
    {
        "name": "🧨 Threats & Violence Filter",
        "keywords": [
            "i will kill you", "gonna kill you", "i'll kill you",
            "i will hurt you", "i will beat you", "imma shoot you",
            "die bitch", "kys", "kill yourself", "end yourself",
            "hang yourself", "drink bleach", "i hope you die",
        ],
    },
    {
        "name": "🔗 Scam & Spam Filter",
        "keywords": [
            "free nitro", "discord nitro free", "claim your nitro",
            "gift card giveaway", "steam gift card", "you have been selected",
            "click here to claim", "bit.ly", "tinyurl.com",
            "crypto investment", "double your bitcoin", "dm me for profit",
            "work from home earn", "make money fast",
        ],
    },
    {
        "name": "🏠 Doxxing Prevention Filter",
        "keywords": [
            "your ip is", "your address is", "i know where you live",
            "doxxed", "doxxing", "swatting", "i will swat", "ssn",
            "social security number", "your real name is", "found your house",
        ],
    },
]

@bot.tree.command(
    name="deploy-rules",
    description="Create 6 keyword AutoMod rules in this server (owner/admin only)"
)
@app_commands.checks.has_permissions(manage_guild=True)
async def deploy_rules(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    if not guild:
        await interaction.followup.send("❌ Must be used inside a server.", ephemeral=True)
        return

    if not guild.me.guild_permissions.manage_guild:
        await interaction.followup.send(
            "❌ I need the **Manage Server** permission to create AutoMod rules.", ephemeral=True
        )
        return

    created, failed, skipped = [], [], []

    # Avoid duplicate names
    existing_names = {r.name for r in await guild.fetch_automod_rules()}

    for rule_def in _DEPLOY_RULES:
        if rule_def["name"] in existing_names:
            skipped.append(rule_def["name"])
            continue
        try:
            trigger = discord.AutoModTrigger(
                keyword_filter=rule_def["keywords"],
            )
            action = discord.AutoModRuleAction(
                type=discord.AutoModRuleActionType.block_message,
            )
            await guild.create_automod_rule(
                name=rule_def["name"],
                event_type=discord.AutoModRuleEventType.message_send,
                trigger=trigger,
                actions=[action],
                enabled=True,
                reason=f"Deployed by {interaction.user} via /deploy-rules",
            )
            created.append(rule_def["name"])
        except discord.HTTPException as e:
            failed.append(f"{rule_def['name']} — {e.text}")

    embed = discord.Embed(
        title="🛡️ AutoMod Rules Deployed",
        color=discord.Color.from_rgb(88, 101, 242),
        timestamp=datetime.datetime.utcnow(),
    )
    if created:
        embed.add_field(
            name=f"✅ Created ({len(created)})",
            value="\n".join(created),
            inline=False,
        )
    if skipped:
        embed.add_field(
            name=f"⏭️ Already Existed ({len(skipped)})",
            value="\n".join(skipped),
            inline=False,
        )
    if failed:
        embed.add_field(
            name=f"❌ Failed ({len(failed)})",
            value="\n".join(failed),
            inline=False,
        )
    total = len(created) + len(skipped)
    embed.set_footer(text=f"{total}/6 rules active • AutoMod badge requires 6 enabled rules")
    await interaction.followup.send(embed=embed, ephemeral=True)

# ── Welcome events ────────────────────────────────────────────────────────────
@bot.event
async def on_member_join(member: discord.Member):
    gid = str(member.guild.id)
    cid = welcome_channels.get(gid)
    if not cid:
        return
    ch = member.guild.get_channel(int(cid))
    if not ch:
        return
    embed = discord.Embed(
        title=f"👋 Welcome to {member.guild.name}!",
        description=f"Hey {member.mention}, glad you're here! You are member **#{member.guild.member_count}**.",
        color=discord.Color.green()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Joined • {datetime.datetime.now().strftime('%B %d, %Y')}")
    await ch.send(embed=embed)

@bot.event
async def on_member_remove(member: discord.Member):
    gid = str(member.guild.id)
    cid = welcome_channels.get(gid)
    if not cid:
        return
    ch = member.guild.get_channel(int(cid))
    if not ch:
        return
    embed = discord.Embed(
        title=f"👋 {member.display_name} has left the server.",
        description=f"We now have **{member.guild.member_count}** members.",
        color=discord.Color.red()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    await ch.send(embed=embed)

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1 — GENERAL
# ═════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="ping", description="Check the bot's speed")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! Latency: **{round(bot.latency * 1000)}ms**")

@bot.tree.command(name="hello", description="Get a friendly greeting")
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f"Hello {interaction.user.mention}! Hope you are having a great day! 😊")

@bot.tree.command(name="bye", description="Say goodbye")
async def bye(interaction: discord.Interaction):
    await interaction.response.send_message(f"Bye {interaction.user.mention}! Hope you have a wonderful day!")

@bot.tree.command(name="botinfo", description="Show info about this bot")
async def botinfo(interaction: discord.Interaction):
    embed = discord.Embed(title="🤖 Bot Info", color=discord.Color.blurple())
    embed.add_field(name="Name", value=bot.user.name, inline=True)
    embed.add_field(name="Servers", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="Latency", value=f"{round(bot.latency*1000)}ms", inline=True)
    embed.add_field(name="Commands", value="30+", inline=True)
    embed.set_thumbnail(url=bot.user.display_avatar.url)
    await interaction.response.send_message(embed=embed)

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2 — INFO
# ═════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="userinfo", description="Show detailed info about a member")
@app_commands.describe(member="The member to look up (defaults to you)")
async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    gid = str(interaction.guild.id)
    uid = str(member.id)
    warn_count = len(warnings_data.get(gid, {}).get(uid, []))
    roles = [r.mention for r in member.roles if r.name != "@everyone"]
    embed = discord.Embed(title=f"👤 {member.display_name}", color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Username", value=str(member), inline=True)
    embed.add_field(name="ID", value=member.id, inline=True)
    embed.add_field(name="Bot?", value="Yes" if member.bot else "No", inline=True)
    embed.add_field(name="Account Created", value=member.created_at.strftime("%b %d, %Y"), inline=True)
    embed.add_field(name="Joined Server", value=member.joined_at.strftime("%b %d, %Y") if member.joined_at else "Unknown", inline=True)
    embed.add_field(name="Warnings", value=str(warn_count), inline=True)
    embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles) if roles else "None", inline=False)
    embed.set_footer(text=f"Requested by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="serverinfo", description="Show info about this server")
async def serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    embed = discord.Embed(title=f"🏠 {g.name}", color=discord.Color.blurple())
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name="Owner", value=g.owner.mention if g.owner else "Unknown", inline=True)
    embed.add_field(name="Members", value=str(g.member_count), inline=True)
    embed.add_field(name="Channels", value=str(len(g.channels)), inline=True)
    embed.add_field(name="Roles", value=str(len(g.roles)), inline=True)
    embed.add_field(name="Boosts", value=str(g.premium_subscription_count), inline=True)
    embed.add_field(name="Created", value=g.created_at.strftime("%b %d, %Y"), inline=True)
    embed.add_field(name="Verification", value=str(g.verification_level).title(), inline=True)
    embed.set_footer(text=f"ID: {g.id}")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="avatar", description="Show a member's avatar")
@app_commands.describe(member="The member whose avatar to show")
async def avatar(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    embed = discord.Embed(title=f"{member.display_name}'s Avatar", color=discord.Color.blurple())
    embed.set_image(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="roleinfo", description="Show info about a role")
@app_commands.describe(role="The role to inspect")
async def roleinfo(interaction: discord.Interaction, role: discord.Role):
    embed = discord.Embed(title=f"🎭 Role: {role.name}", color=role.color)
    embed.add_field(name="ID", value=role.id, inline=True)
    embed.add_field(name="Members", value=str(len(role.members)), inline=True)
    embed.add_field(name="Mentionable", value="Yes" if role.mentionable else "No", inline=True)
    embed.add_field(name="Hoisted", value="Yes" if role.hoist else "No", inline=True)
    embed.add_field(name="Created", value=role.created_at.strftime("%b %d, %Y"), inline=True)
    embed.add_field(name="Position", value=str(role.position), inline=True)
    await interaction.response.send_message(embed=embed)

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3 — POLLS & DICE
# ═════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="roll", description="Roll dice — e.g. 2d6")
@app_commands.describe(dice="Dice in NdN format (e.g. 2d6, 1d20). Defaults to 1d6.")
async def roll(interaction: discord.Interaction, dice: str = "1d6"):
    try:
        parts = dice.lower().split("d")
        if len(parts) != 2:
            raise ValueError
        count, sides = int(parts[0]), int(parts[1])
        if count < 1 or count > 100 or sides < 2 or sides > 1000:
            raise ValueError
    except (ValueError, IndexError):
        await interaction.response.send_message("❌ Invalid format. Use NdN e.g. `2d6`.", ephemeral=True)
        return
    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls)
    rolls_str = ", ".join(str(r) for r in rolls)
    if count == 1:
        await interaction.response.send_message(f"🎲 Rolled **d{sides}**: **{total}**")
    else:
        await interaction.response.send_message(f"🎲 Rolled **{dice}**: [{rolls_str}] → Total: **{total}**")

@bot.tree.command(name="poll", description="Create a quick yes/no poll")
@app_commands.describe(question="The question to ask")
async def poll(interaction: discord.Interaction, question: str):
    embed = discord.Embed(title="📊 Poll", description=question, color=discord.Color.blurple())
    embed.set_footer(text=f"Asked by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    await msg.add_reaction("👍")
    await msg.add_reaction("👎")

@bot.tree.command(name="poll_choice", description="Create a poll with up to 4 custom options")
@app_commands.describe(question="The question", option1="Option 1", option2="Option 2",
                        option3="Option 3 (optional)", option4="Option 4 (optional)")
async def poll_choice(interaction: discord.Interaction, question: str, option1: str, option2: str,
                       option3: str = None, option4: str = None):
    emojis = ["1️⃣","2️⃣","3️⃣","4️⃣"]
    options = [o for o in [option1, option2, option3, option4] if o]
    desc = "\n".join(f"{emojis[i]} {o}" for i, o in enumerate(options))
    embed = discord.Embed(title=f"📊 {question}", description=desc, color=discord.Color.blurple())
    embed.set_footer(text=f"Asked by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    for i in range(len(options)):
        await msg.add_reaction(emojis[i])

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 4 — WELCOME SYSTEM (owner only)
# ═════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="setwelcome", description="Set the channel for welcome/leave messages (owner only)")
@app_commands.describe(channel="The channel to use")
@app_commands.check(is_owner)
async def setwelcome(interaction: discord.Interaction, channel: discord.TextChannel):
    welcome_channels[str(interaction.guild.id)] = str(channel.id)
    save_json(WELCOME_FILE, welcome_channels)
    await interaction.response.send_message(f"✅ Welcome messages will be sent in {channel.mention}.")

@bot.tree.command(name="clearwelcome", description="Disable welcome messages (owner only)")
@app_commands.check(is_owner)
async def clearwelcome(interaction: discord.Interaction):
    welcome_channels.pop(str(interaction.guild.id), None)
    save_json(WELCOME_FILE, welcome_channels)
    await interaction.response.send_message("✅ Welcome messages disabled.")

@bot.tree.command(name="welcometest", description="Preview the welcome message (owner only)")
@app_commands.check(is_owner)
async def welcometest(interaction: discord.Interaction):
    m = interaction.user
    embed = discord.Embed(
        title=f"👋 Welcome to {interaction.guild.name}!",
        description=f"Hey {m.mention}, glad you're here! You are member **#{interaction.guild.member_count}**.",
        color=discord.Color.green()
    )
    embed.set_thumbnail(url=m.display_avatar.url)
    embed.set_footer(text=f"Joined • {datetime.datetime.now().strftime('%B %d, %Y')}")
    await interaction.response.send_message("Preview:", embed=embed, ephemeral=True)

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 5 — MODERATION (owner only)
# ═════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="kick", description="Kick a member (owner only)")
@app_commands.describe(member="Member to kick", reason="Reason")
@app_commands.check(is_owner)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if member == interaction.user:
        return await interaction.response.send_message("❌ Can't kick yourself.", ephemeral=True)
    if member.top_role >= interaction.guild.me.top_role:
        return await interaction.response.send_message("❌ I can't kick that member.", ephemeral=True)
    await member.kick(reason=reason)
    await interaction.response.send_message(f"👢 **{member.display_name}** kicked. Reason: {reason}")

@bot.tree.command(name="ban", description="Ban a member (owner only)")
@app_commands.describe(member="Member to ban", reason="Reason")
@app_commands.check(is_owner)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if member == interaction.user:
        return await interaction.response.send_message("❌ Can't ban yourself.", ephemeral=True)
    if member.top_role >= interaction.guild.me.top_role:
        return await interaction.response.send_message("❌ I can't ban that member.", ephemeral=True)
    try:
        await member.send(f"🔨 You have been banned from **{interaction.guild.name}**. Reason: {reason}")
    except discord.Forbidden:
        pass
    await member.ban(reason=reason)
    await interaction.response.send_message(f"🔨 **{member.display_name}** banned. Reason: {reason}")

@bot.tree.command(name="softban", description="Ban then immediately unban a member to delete their messages (owner only)")
@app_commands.describe(member="Member to softban", reason="Reason")
@app_commands.check(is_owner)
async def softban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if member.top_role >= interaction.guild.me.top_role:
        return await interaction.response.send_message("❌ I can't softban that member.", ephemeral=True)
    await member.ban(reason=f"Softban: {reason}", delete_message_days=7)
    await interaction.guild.unban(member, reason="Softban unban")
    await interaction.response.send_message(f"🧹 **{member.display_name}** softbanned (messages deleted, not permanently banned).")

@bot.tree.command(name="unban", description="Unban a user by ID (owner only)")
@app_commands.describe(user_id="Discord user ID")
@app_commands.check(is_owner)
async def unban(interaction: discord.Interaction, user_id: str):
    try:
        uid = int(user_id)
    except ValueError:
        return await interaction.response.send_message("❌ Invalid ID.", ephemeral=True)
    try:
        user = await bot.fetch_user(uid)
        await interaction.guild.unban(user)
        await interaction.response.send_message(f"✅ **{user.name}** unbanned.")
    except discord.NotFound:
        await interaction.response.send_message("❌ User not found or not banned.", ephemeral=True)

@bot.tree.command(name="banlist", description="Show all banned users (owner only)")
@app_commands.check(is_owner)
async def banlist(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    bans = [entry async for entry in interaction.guild.bans()]
    if not bans:
        return await interaction.followup.send("✅ No banned users.", ephemeral=True)
    lines = [f"**{e.user}** — {e.reason or 'No reason'}" for e in bans[:20]]
    embed = discord.Embed(title=f"🔨 Ban List ({len(bans)} total)", description="\n".join(lines), color=discord.Color.red())
    if len(bans) > 20:
        embed.set_footer(text=f"Showing 20 of {len(bans)}")
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="timeout", description="Timeout a member (owner only)")
@app_commands.describe(member="Member to timeout", minutes="Duration in minutes (1–40320)")
@app_commands.check(is_owner)
async def timeout(interaction: discord.Interaction, member: discord.Member, minutes: int = 5):
    if minutes < 1 or minutes > 40320:
        return await interaction.response.send_message("❌ Duration must be 1–40320 minutes.", ephemeral=True)
    await member.timeout(datetime.timedelta(minutes=minutes))
    await interaction.response.send_message(f"⏱️ **{member.display_name}** timed out for {minutes} minute(s).")

@bot.tree.command(name="untimeout", description="Remove a timeout from a member (owner only)")
@app_commands.describe(member="Member to un-timeout")
@app_commands.check(is_owner)
async def untimeout(interaction: discord.Interaction, member: discord.Member):
    await member.timeout(None)
    await interaction.response.send_message(f"✅ Timeout removed from **{member.display_name}**.")

@bot.tree.command(name="clear", description="Delete messages from this channel (owner only)")
@app_commands.describe(amount="Number of messages to delete (1–100)")
@app_commands.check(is_owner)
async def clear(interaction: discord.Interaction, amount: int = 10):
    if amount < 1 or amount > 100:
        return await interaction.response.send_message("❌ Amount must be 1–100.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"🧹 Deleted {len(deleted)} message(s).", ephemeral=True)

@bot.tree.command(name="slowmode", description="Set slowmode on this channel (owner only)")
@app_commands.describe(seconds="Slowmode delay in seconds (0 to disable, max 21600)")
@app_commands.check(is_owner)
async def slowmode(interaction: discord.Interaction, seconds: int = 0):
    if seconds < 0 or seconds > 21600:
        return await interaction.response.send_message("❌ Must be 0–21600 seconds.", ephemeral=True)
    await interaction.channel.edit(slowmode_delay=seconds)
    if seconds == 0:
        await interaction.response.send_message("✅ Slowmode disabled.")
    else:
        await interaction.response.send_message(f"🐢 Slowmode set to **{seconds}** second(s).")

@bot.tree.command(name="lock", description="Lock this channel so members can't send messages (owner only)")
@app_commands.describe(reason="Reason for locking")
@app_commands.check(is_owner)
async def lock(interaction: discord.Interaction, reason: str = "No reason provided"):
    overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = False
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    embed = discord.Embed(title="🔒 Channel Locked", description=f"Reason: {reason}", color=discord.Color.red())
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="unlock", description="Unlock this channel (owner only)")
@app_commands.check(is_owner)
async def unlock(interaction: discord.Interaction):
    overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = None
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
    embed = discord.Embed(title="🔓 Channel Unlocked", color=discord.Color.green())
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="nick", description="Change a member's nickname (owner only)")
@app_commands.describe(member="The member", nickname="New nickname (leave blank to reset)")
@app_commands.check(is_owner)
async def nick(interaction: discord.Interaction, member: discord.Member, nickname: str = None):
    await member.edit(nick=nickname)
    if nickname:
        await interaction.response.send_message(f"✅ Nickname changed to **{nickname}** for {member.mention}.")
    else:
        await interaction.response.send_message(f"✅ Nickname reset for {member.mention}.")

@bot.tree.command(name="addrole", description="Add a role to a member (owner only)")
@app_commands.describe(member="The member", role="The role to add")
@app_commands.check(is_owner)
async def addrole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if role in member.roles:
        return await interaction.response.send_message(f"❌ {member.mention} already has {role.mention}.", ephemeral=True)
    await member.add_roles(role)
    await interaction.response.send_message(f"✅ Added {role.mention} to {member.mention}.")

@bot.tree.command(name="removerole", description="Remove a role from a member (owner only)")
@app_commands.describe(member="The member", role="The role to remove")
@app_commands.check(is_owner)
async def removerole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if role not in member.roles:
        return await interaction.response.send_message(f"❌ {member.mention} doesn't have {role.mention}.", ephemeral=True)
    await member.remove_roles(role)
    await interaction.response.send_message(f"✅ Removed {role.mention} from {member.mention}.")

@bot.tree.command(name="announce", description="Send an announcement embed (owner only)")
@app_commands.describe(message="The announcement text", channel="Channel to send it in (defaults to current)")
@app_commands.check(is_owner)
async def announce(interaction: discord.Interaction, message: str, channel: discord.TextChannel = None):
    ch = channel or interaction.channel
    embed = discord.Embed(title="📢 Announcement", description=message, color=discord.Color.gold())
    embed.set_footer(text=f"Posted by {interaction.user.display_name} • {datetime.datetime.now().strftime('%B %d, %Y')}")
    await ch.send(embed=embed)
    await interaction.response.send_message(f"✅ Announcement sent in {ch.mention}.", ephemeral=True)

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 6 — WARNING SYSTEM (owner only)
# ═════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="setwarnrole", description="Set the role assigned to warned members (owner only)")
@app_commands.describe(role="The warning role")
@app_commands.check(is_owner)
async def setwarnrole(interaction: discord.Interaction, role: discord.Role):
    warn_roles[str(interaction.guild.id)] = str(role.id)
    save_json(WARN_ROLES_FILE, warn_roles)
    await interaction.response.send_message(f"✅ Warned members will receive {role.mention}.")

@bot.tree.command(name="warn", description="Warn a member (owner only)")
@app_commands.describe(member="Member to warn", reason="Reason")
@app_commands.check(is_owner)
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if member.bot:
        return await interaction.response.send_message("❌ Can't warn a bot.", ephemeral=True)
    # Self-warning is allowed — server owners can use it for testing
    gid, uid = str(interaction.guild.id), str(member.id)
    warnings_data.setdefault(gid, {}).setdefault(uid, [])
    warnings_data[gid][uid].append({
        "reason": reason,
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "by": str(interaction.user)
    })
    save_json(WARNINGS_FILE, warnings_data)
    count = len(warnings_data[gid][uid])
    role_text = ""
    rid = warn_roles.get(gid)
    if rid:
        wr = interaction.guild.get_role(int(rid))
        if wr and wr not in member.roles:
            try:
                await member.add_roles(wr)
                role_text = f" Role **{wr.name}** assigned."
            except discord.Forbidden:
                role_text = " ⚠️ Couldn't assign warn role."
    embed = discord.Embed(title="⚠️ Member Warned", color=discord.Color.orange())
    embed.add_field(name="Member", value=member.mention, inline=True)
    embed.add_field(name="Warnings", value=str(count), inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text=f"By {interaction.user.display_name}")
    await interaction.response.send_message(content=role_text.strip() or None, embed=embed)
    try:
        await member.send(f"⚠️ You were warned in **{interaction.guild.name}**.\n**Reason:** {reason}\n**Total warnings:** {count}")
    except discord.Forbidden:
        pass

@bot.tree.command(name="warnings", description="View a member's warnings")
@app_commands.describe(member="The member to check")
async def warnings(interaction: discord.Interaction, member: discord.Member):
    gid, uid = str(interaction.guild.id), str(member.id)
    entries = warnings_data.get(gid, {}).get(uid, [])
    if not entries:
        return await interaction.response.send_message(f"✅ **{member.display_name}** has no warnings.", ephemeral=True)
    embed = discord.Embed(title=f"⚠️ Warnings for {member.display_name}", color=discord.Color.orange())
    for i, w in enumerate(entries, 1):
        embed.add_field(name=f"#{i} — {w['timestamp']}", value=f"**Reason:** {w['reason']}\n**By:** {w['by']}", inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="clearwarnings", description="Clear all warnings for a member (owner only)")
@app_commands.describe(member="Member to clear")
@app_commands.check(is_owner)
async def clearwarnings(interaction: discord.Interaction, member: discord.Member):
    gid, uid = str(interaction.guild.id), str(member.id)
    warnings_data.get(gid, {}).pop(uid, None)
    save_json(WARNINGS_FILE, warnings_data)
    rid = warn_roles.get(gid)
    role_text = ""
    if rid:
        wr = interaction.guild.get_role(int(rid))
        if wr and wr in member.roles:
            try:
                await member.remove_roles(wr)
                role_text = f" Role **{wr.name}** removed."
            except discord.Forbidden:
                role_text = " ⚠️ Couldn't remove warn role."
    await interaction.response.send_message(f"✅ Warnings cleared for **{member.display_name}**.{role_text}")

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 7 — ENTERTAINMENT
# ═════════════════════════════════════════════════════════════════════════════

@bot.tree.command(name="coinflip", description="Flip a coin")
async def coinflip(interaction: discord.Interaction):
    result = random.choice(["Heads 🪙", "Tails 🪙"])
    await interaction.response.send_message(f"**{result}!**")

@bot.tree.command(name="8ball", description="Ask the magic 8-ball a question")
@app_commands.describe(question="Your yes/no question")
async def eightball(interaction: discord.Interaction, question: str):
    responses = [
        "It is certain.","It is decidedly so.","Without a doubt.","Yes, definitely.",
        "You may rely on it.","As I see it, yes.","Most likely.","Outlook good.",
        "Yes.","Signs point to yes.","Reply hazy, try again.","Ask again later.",
        "Better not tell you now.","Cannot predict now.","Concentrate and ask again.",
        "Don't count on it.","My reply is no.","My sources say no.",
        "Outlook not so good.","Very doubtful."
    ]
    embed = discord.Embed(title="🎱 Magic 8-Ball", color=discord.Color.dark_purple())
    embed.add_field(name="Question", value=question, inline=False)
    embed.add_field(name="Answer", value=random.choice(responses), inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="joke", description="Get a random joke")
async def joke(interaction: discord.Interaction):
    jokes = [
        ("Why don't scientists trust atoms?", "Because they make up everything!"),
        ("Why did the scarecrow win an award?", "Because he was outstanding in his field!"),
        ("I told my wife she was drawing her eyebrows too high.", "She looked surprised."),
        ("Why don't eggs tell jokes?", "They'd crack each other up!"),
        ("What do you call a fake noodle?", "An impasta!"),
        ("Why can't you give Elsa a balloon?", "Because she'll let it go!"),
        ("What do you call cheese that isn't yours?", "Nacho cheese!"),
        ("Why did the bicycle fall over?", "Because it was two-tired!"),
        ("What do you call a sleeping dinosaur?", "A dino-snore!"),
        ("Why did the math book look so sad?", "It had too many problems."),
        ("What do you call a bear with no teeth?", "A gummy bear!"),
        ("Why did the golfer bring an extra pair of pants?", "In case he got a hole in one!"),
        ("How do you organize a space party?", "You planet!"),
        ("What's brown and sticky?", "A stick!"),
        ("Why did the tomato turn red?", "Because it saw the salad dressing!"),
    ]
    setup, punchline = random.choice(jokes)
    embed = discord.Embed(title="😂 Joke Time", color=discord.Color.yellow())
    embed.add_field(name="Setup", value=setup, inline=False)
    embed.add_field(name="Punchline", value=punchline, inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="fact", description="Get a random fun fact")
async def fact(interaction: discord.Interaction):
    facts = [
        "Honey never expires. Archaeologists have found 3,000-year-old honey in Egyptian tombs.",
        "A group of flamingos is called a flamboyance.",
        "Crows can recognize human faces and hold grudges against people who have wronged them.",
        "Octopuses have three hearts, nine brains, and blue blood.",
        "Bananas are berries, but strawberries aren't.",
        "There are more possible iterations of a game of chess than there are atoms in the observable universe.",
        "A day on Venus is longer than a year on Venus.",
        "Sharks are older than trees. They've existed for around 450 million years.",
        "Cleopatra lived closer in time to the Moon landing than to the construction of the Great Pyramid.",
        "A bolt of lightning is five times hotter than the surface of the sun.",
        "The human body contains enough carbon to make about 9,000 pencils.",
        "Wombat poop is cube-shaped — the only animal to produce cube feces.",
        "There is enough DNA in the human body to stretch from the sun to Pluto and back 17 times.",
        "The world's oldest known living tree is over 5,000 years old.",
        "Butterflies taste with their feet.",
        "A group of owls is called a parliament.",
        "It would take about 100,000 years to travel across the Milky Way at the speed of light.",
    ]
    embed = discord.Embed(title="🧠 Fun Fact", description=random.choice(facts), color=discord.Color.teal())
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="rps", description="Play Rock Paper Scissors against the bot")
@app_commands.describe(choice="Your choice")
@app_commands.choices(choice=[
    app_commands.Choice(name="Rock 🪨", value="rock"),
    app_commands.Choice(name="Paper 📄", value="paper"),
    app_commands.Choice(name="Scissors ✂️", value="scissors"),
])
async def rps(interaction: discord.Interaction, choice: app_commands.Choice[str]):
    options = ["rock","paper","scissors"]
    emojis = {"rock":"🪨","paper":"📄","scissors":"✂️"}
    bot_choice = random.choice(options)
    wins = {"rock":"scissors","paper":"rock","scissors":"paper"}
    if choice.value == bot_choice:
        result = "It's a tie! 🤝"
        color = discord.Color.yellow()
    elif wins[choice.value] == bot_choice:
        result = "You win! 🎉"
        color = discord.Color.green()
    else:
        result = "I win! 😎"
        color = discord.Color.red()
    embed = discord.Embed(title="✂️ Rock Paper Scissors", color=color)
    embed.add_field(name="Your choice", value=emojis[choice.value], inline=True)
    embed.add_field(name="My choice", value=emojis[bot_choice], inline=True)
    embed.add_field(name="Result", value=result, inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="choose", description="Let the bot choose between options for you")
@app_commands.describe(options="Comma-separated options e.g. pizza, sushi, tacos")
async def choose(interaction: discord.Interaction, options: str):
    choices = [o.strip() for o in options.split(",") if o.strip()]
    if len(choices) < 2:
        return await interaction.response.send_message("❌ Provide at least 2 comma-separated options.", ephemeral=True)
    await interaction.response.send_message(f"🎯 I choose: **{random.choice(choices)}**")

@bot.tree.command(name="rate", description="Let the bot rate something out of 10")
@app_commands.describe(thing="What to rate")
async def rate(interaction: discord.Interaction, thing: str):
    score = random.randint(0, 10)
    bar = "█" * score + "░" * (10 - score)
    await interaction.response.send_message(f"📊 I rate **{thing}** a **{score}/10**\n`{bar}`")

@bot.tree.command(name="ship", description="Check the compatibility between two members")
@app_commands.describe(user1="First person", user2="Second person")
async def ship(interaction: discord.Interaction, user1: discord.Member, user2: discord.Member):
    score = random.randint(0, 100)
    bar = "❤️" * (score // 10) + "🖤" * (10 - score // 10)
    if score < 30:
        verdict = "Not a great match 💔"
    elif score < 60:
        verdict = "There's potential! 💛"
    elif score < 85:
        verdict = "Great match! 💕"
    else:
        verdict = "Perfect match! 💞"
    embed = discord.Embed(title="💘 Compatibility Check", color=discord.Color.pink())
    embed.add_field(name="Couple", value=f"{user1.mention} + {user2.mention}", inline=False)
    embed.add_field(name="Score", value=f"**{score}%** {bar}", inline=False)
    embed.add_field(name="Verdict", value=verdict, inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="roast", description="Roast a member (all in good fun!)")
@app_commands.describe(member="Who to roast")
async def roast(interaction: discord.Interaction, member: discord.Member):
    roasts = [
        f"{member.mention}, you're like a cloud. When you disappear, it's a beautiful day.",
        f"I'd roast {member.mention} but my mom said I'm not allowed to burn trash.",
        f"{member.mention}, you're proof that even evolution makes mistakes.",
        f"Talking to {member.mention} is like reading a book with no words — pointless.",
        f"{member.mention}, your Wi-Fi password is probably 'password123'.",
        f"I've seen better comebacks than {member.mention} in a boomerang factory.",
        f"{member.mention}, if brains were gasoline, you couldn't power a go-kart.",
        f"The village called, {member.mention}. They want their idiot back.",
        f"{member.mention}, you're not stupid — you just have bad luck thinking.",
        f"I'd agree with {member.mention}, but then we'd both be wrong.",
    ]
    await interaction.response.send_message(random.choice(roasts))

@bot.tree.command(name="compliment", description="Compliment a member")
@app_commands.describe(member="Who to compliment")
async def compliment(interaction: discord.Interaction, member: discord.Member):
    compliments = [
        f"{member.mention}, you light up every room you walk into! ✨",
        f"{member.mention} is genuinely one of the kindest people here. 💛",
        f"The server is way better because {member.mention} is in it. 🌟",
        f"{member.mention}, your positive energy is contagious! 🌈",
        f"{member.mention} is the kind of person who makes everyone feel welcome. 🤗",
        f"If there was an award for being awesome, {member.mention} would win every time. 🏆",
        f"{member.mention}, you're an absolute legend and don't let anyone tell you otherwise. 🔥",
        f"The world needs more people like {member.mention}. Keep being amazing! 💪",
    ]
    await interaction.response.send_message(random.choice(compliments))

@bot.tree.command(name="mock", description="Convert text to SpOnGeBoB mocking style")
@app_commands.describe(text="Text to mock")
async def mock(interaction: discord.Interaction, text: str):
    result = "".join(c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(text))
    await interaction.response.send_message(f"🧽 {result}")

@bot.tree.command(name="reverse", description="Reverse some text")
@app_commands.describe(text="Text to reverse")
async def reverse(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(f"🔄 {text[::-1]}")

@bot.tree.command(name="would_you_rather", description="Get a random would-you-rather question")
async def would_you_rather(interaction: discord.Interaction):
    questions = [
        ("be able to fly", "be invisible"),
        ("only eat pizza for a year", "only drink water for a year"),
        ("have super strength", "have super speed"),
        ("know when you're going to die", "know how you're going to die"),
        ("always be 10 minutes late", "always be 20 minutes early"),
        ("live without music", "live without TV/movies"),
        ("be incredibly smart but poor", "be incredibly rich but average intelligence"),
        ("never use social media again", "never watch Netflix again"),
        ("have a rewind button for your life", "a pause button"),
        ("speak every language", "play every musical instrument"),
        ("fight 100 duck-sized horses", "one horse-sized duck"),
        ("always have to sing instead of speak", "always have to dance instead of walk"),
    ]
    a, b = random.choice(questions)
    embed = discord.Embed(title="🤔 Would You Rather...", color=discord.Color.purple())
    embed.add_field(name="Option A", value=f"🅰️ {a.capitalize()}", inline=True)
    embed.add_field(name="Option B", value=f"🅱️ {b.capitalize()}", inline=True)
    await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    await msg.add_reaction("🅰️")
    await msg.add_reaction("🅱️")

@bot.tree.command(name="trivia", description="Get a random trivia question")
async def trivia(interaction: discord.Interaction):
    questions = [
        ("What planet is known as the Red Planet?", "Mars"),
        ("How many sides does a hexagon have?", "6"),
        ("What is the capital of Japan?", "Tokyo"),
        ("Who painted the Mona Lisa?", "Leonardo da Vinci"),
        ("What is the chemical symbol for gold?", "Au"),
        ("How many bones are in the adult human body?", "206"),
        ("What is the largest ocean on Earth?", "Pacific Ocean"),
        ("Who wrote Romeo and Juliet?", "William Shakespeare"),
        ("What year did World War II end?", "1945"),
        ("What is the speed of light (approx)?", "300,000 km/s"),
        ("What language has the most native speakers in the world?", "Mandarin Chinese"),
        ("What is the smallest planet in the solar system?", "Mercury"),
    ]
    q, a = random.choice(questions)
    embed = discord.Embed(title="🧩 Trivia", description=q, color=discord.Color.orange())
    embed.set_footer(text="Reply with your answer! The answer will be revealed shortly.")
    await interaction.response.send_message(embed=embed)
    await discord.utils.sleep_until(datetime.datetime.now() + datetime.timedelta(seconds=15))
    reveal = discord.Embed(title="✅ Answer", description=f"**{a}**", color=discord.Color.green())
    await interaction.followup.send(embed=reveal)

@bot.tree.command(name="number_guess", description="Try to guess a number between 1 and 10")
@app_commands.describe(guess="Your guess (1–10)")
async def number_guess(interaction: discord.Interaction, guess: int):
    if guess < 1 or guess > 10:
        return await interaction.response.send_message("❌ Guess must be between 1 and 10.", ephemeral=True)
    answer = random.randint(1, 10)
    if guess == answer:
        await interaction.response.send_message(f"🎉 Correct! The number was **{answer}**! You guessed it!")
    else:
        await interaction.response.send_message(f"❌ Wrong! The number was **{answer}**. Better luck next time!")

@bot.tree.command(name="slots", description="Play the slot machine!")
async def slots(interaction: discord.Interaction):
    symbols = ["🍒","🍋","🍊","🍇","⭐","💎","7️⃣"]
    s = [random.choice(symbols) for _ in range(3)]
    result = " | ".join(s)
    if s[0] == s[1] == s[2] == "💎":
        outcome = "🤑 **JACKPOT! DIAMONDS!** You hit the ultimate prize!"
    elif s[0] == s[1] == s[2] == "7️⃣":
        outcome = "🎰 **TRIPLE 7s!** Massive win!"
    elif s[0] == s[1] == s[2]:
        outcome = f"🎉 **Triple {s[0]}!** You win!"
    elif s[0] == s[1] or s[1] == s[2] or s[0] == s[2]:
        outcome = "😊 **Two of a kind!** Small win!"
    else:
        outcome = "😔 No match. Better luck next time!"
    embed = discord.Embed(title="🎰 Slot Machine", color=discord.Color.gold())
    embed.add_field(name="Result", value=f"[ {result} ]", inline=False)
    embed.add_field(name="Outcome", value=outcome, inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="pp", description="Check someone's pp size 👀")
@app_commands.describe(member="Who to check (defaults to you)")
async def pp(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    size = random.randint(0, 20)
    bar = "8" + "=" * size + "D"
    await interaction.response.send_message(f"📏 **{member.display_name}'s pp:**\n`{bar}`\n*{size} inches*")

@bot.tree.command(name="iq", description="Check someone's IQ")
@app_commands.describe(member="Who to test (defaults to you)")
async def iq(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    score = random.randint(1, 200)
    if score < 70:
        verdict = "💀 Oof."
    elif score < 90:
        verdict = "😅 Room for improvement."
    elif score < 110:
        verdict = "😐 About average."
    elif score < 130:
        verdict = "🤓 Pretty smart!"
    elif score < 160:
        verdict = "🧠 Big brain energy!"
    else:
        verdict = "👽 Galaxy brain. Are you even human?"
    await interaction.response.send_message(f"🧠 **{member.display_name}'s IQ: {score}** — {verdict}")

@bot.tree.command(name="sus", description="How sus is someone?")
@app_commands.describe(member="Who to check")
async def sus(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    level = random.randint(0, 100)
    bar = "🟥" * (level // 10) + "⬛" * (10 - level // 10)
    if level > 85:
        verdict = "🚨 VERY SUS. Eject immediately."
    elif level > 60:
        verdict = "🤔 Pretty suspicious."
    elif level > 30:
        verdict = "😐 Kinda sus."
    else:
        verdict = "✅ Not sus at all."
    await interaction.response.send_message(f"📡 **{member.display_name}** sus level: **{level}%**\n{bar}\n{verdict}")

@bot.tree.command(name="hug", description="Hug a member")
@app_commands.describe(member="Who to hug")
async def hug(interaction: discord.Interaction, member: discord.Member):
    messages = [
        f"{interaction.user.mention} gives {member.mention} a big warm hug! 🤗",
        f"{interaction.user.mention} wraps {member.mention} in a cozy hug! 💛",
        f"{member.mention} receives a surprise hug from {interaction.user.mention}! 🫂",
    ]
    await interaction.response.send_message(random.choice(messages))

@bot.tree.command(name="slap", description="Slap a member")
@app_commands.describe(member="Who to slap")
async def slap(interaction: discord.Interaction, member: discord.Member):
    messages = [
        f"{interaction.user.mention} slapped {member.mention} with a wet fish! 🐟",
        f"👋 **SLAP!** {interaction.user.mention} smacked {member.mention} into next week!",
        f"{member.mention} got absolutely walloped by {interaction.user.mention}! 💥",
    ]
    await interaction.response.send_message(random.choice(messages))

@bot.tree.command(name="pat", description="Pat a member on the head")
@app_commands.describe(member="Who to pat")
async def pat(interaction: discord.Interaction, member: discord.Member):
    messages = [
        f"{interaction.user.mention} pats {member.mention} on the head. ☺️ *pat pat*",
        f"*pat pat* {member.mention} has been blessed by {interaction.user.mention}! 🙏",
        f"{interaction.user.mention} gives {member.mention} gentle head pats. uwu",
    ]
    await interaction.response.send_message(random.choice(messages))

@bot.tree.command(name="clap", description="Add claps between every word 👏")
@app_commands.describe(text="Text to clappify")
async def clap(interaction: discord.Interaction, text: str):
    await interaction.response.send_message("👏 " + " 👏 ".join(text.split()) + " 👏")

@bot.tree.command(name="emojify", description="Convert your text to emoji letters")
@app_commands.describe(text="Text to emojify")
async def emojify(interaction: discord.Interaction, text: str):
    mapping = {c: f":regional_indicator_{c}:" for c in "abcdefghijklmnopqrstuvwxyz"}
    mapping.update({str(i): ["0️⃣","1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣"][i] for i in range(10)})
    mapping[" "] = "   "
    result = "".join(mapping.get(c.lower(), c) for c in text)
    if len(result) > 2000:
        return await interaction.response.send_message("❌ Text too long to emojify.", ephemeral=True)
    await interaction.response.send_message(result)

@bot.tree.command(name="quote", description="Get a random motivational quote")
async def quote(interaction: discord.Interaction):
    quotes = [
        ("The only way to do great work is to love what you do.", "Steve Jobs"),
        ("In the middle of every difficulty lies opportunity.", "Albert Einstein"),
        ("It does not matter how slowly you go as long as you do not stop.", "Confucius"),
        ("Life is what happens when you're busy making other plans.", "John Lennon"),
        ("The future belongs to those who believe in the beauty of their dreams.", "Eleanor Roosevelt"),
        ("It is during our darkest moments that we must focus to see the light.", "Aristotle"),
        ("Believe you can and you're halfway there.", "Theodore Roosevelt"),
        ("Success is not final, failure is not fatal: it is the courage to continue that counts.", "Winston Churchill"),
        ("You miss 100% of the shots you don't take.", "Wayne Gretzky"),
        ("Whether you think you can or you think you can't, you're right.", "Henry Ford"),
        ("The best time to plant a tree was 20 years ago. The second best time is now.", "Chinese Proverb"),
        ("An unexamined life is not worth living.", "Socrates"),
        ("Spread love everywhere you go. Let no one ever come to you without leaving happier.", "Mother Teresa"),
        ("When you reach the end of your rope, tie a knot in it and hang on.", "Franklin D. Roosevelt"),
        ("Don't judge each day by the harvest you reap but by the seeds that you plant.", "Robert Louis Stevenson"),
    ]
    q, author = random.choice(quotes)
    embed = discord.Embed(description=f'*"{q}"*', color=discord.Color.teal())
    embed.set_footer(text=f"— {author}")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="pickup", description="Get a random pickup line")
async def pickup(interaction: discord.Interaction):
    lines = [
        "Are you a magician? Because whenever I look at you, everyone else disappears.",
        "Do you have a map? I keep getting lost in your eyes.",
        "Are you made of copper and tellurium? Because you're CuTe.",
        "Do you believe in love at first sight, or should I walk by again?",
        "Is your name Google? Because you have everything I've been searching for.",
        "Are you a parking ticket? Because you've got 'fine' written all over you.",
        "Do you have a Band-Aid? Because I just scraped my knee falling for you.",
        "Are you a Wi-Fi signal? Because I'm feeling a connection.",
        "If you were a vegetable, you'd be a cute-cumber.",
        "Are you an interior decorator? Because when I saw you, the entire room became beautiful.",
        "Do you like science? Because I've got great chemistry with you.",
        "Are you a time traveler? Because I can see you in my future.",
    ]
    await interaction.response.send_message(f"💘 {random.choice(lines)}")

@bot.tree.command(name="dare", description="Get a random dare")
async def dare(interaction: discord.Interaction):
    dares = [
        "Send the last photo in your camera roll to this chat.",
        "Talk in an accent for the next 5 minutes.",
        "Change your nickname to something embarrassing for 10 minutes.",
        "Send a compliment to the last person you talked to.",
        "Say the alphabet backwards.",
        "Do your best impression of a famous person.",
        "Tell an embarrassing story about yourself.",
        "Post the most recent meme you saved.",
        "Speak only in questions for the next 3 minutes.",
        "Share your most recent Google search.",
        "Type everything in ALL CAPS for the next 5 minutes.",
        "Do 10 push-ups right now.",
        "Send a voicemail singing 'Happy Birthday' to someone.",
        "Change your profile picture to something silly for 1 hour.",
    ]
    embed = discord.Embed(title="🎯 Dare", description=random.choice(dares), color=discord.Color.red())
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="truth", description="Get a random truth question")
async def truth(interaction: discord.Interaction):
    truths = [
        "What's the most embarrassing thing you've ever done?",
        "Have you ever lied to get out of trouble? What was it?",
        "What's your biggest fear?",
        "What's the most childish thing you still do?",
        "Have you ever cheated on a test?",
        "What's a secret you've never told anyone?",
        "Who was your first crush?",
        "What's the worst gift you've ever received?",
        "Have you ever pretended to like a gift you hated?",
        "What's the silliest reason you've cried?",
        "What's the most ridiculous thing you've ever believed as a kid?",
        "Have you ever sent a text to the wrong person? What did it say?",
        "What's the most embarrassing song on your playlist?",
        "What's a bad habit you can't break?",
    ]
    embed = discord.Embed(title="💬 Truth", description=random.choice(truths), color=discord.Color.blue())
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="fortune", description="Get a fortune cookie message")
async def fortune(interaction: discord.Interaction):
    fortunes = [
        "A beautiful, smart, and loving person will be coming into your life. 🍀",
        "A fresh start will put you on your way. ✨",
        "Your hard work will soon pay off. 💪",
        "Adventure can be real happiness. 🌍",
        "Keep your feet on the ground even though friends flatter you. 🌱",
        "Nothing in the world is accomplished without passion. 🔥",
        "The greatest risk is not taking one. 🎲",
        "Every day is a new opportunity. Seize it! 🌅",
        "Your smile is your greatest asset. 😄",
        "Good things come to those who hustle. 💼",
        "The secret of getting ahead is getting started. 🚀",
        "You will be rewarded for your patience and understanding. 🏅",
        "Something wonderful is about to happen. 🌟",
        "Be not afraid of growing slowly, be afraid only of standing still. 🐢",
    ]
    embed = discord.Embed(title="🥠 Fortune Cookie", description=random.choice(fortunes), color=discord.Color.orange())
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="hack", description="Fake hack someone 💀")
@app_commands.describe(member="Who to 'hack'")
async def hack(interaction: discord.Interaction, member: discord.Member = None):
    target = member.display_name if member else "the mainframe"
    await interaction.response.send_message(f"🔍 Initiating hack on **{target}**...")
    msg = await interaction.original_response()

    bars = [
        "██████████ ██████████ 0%",
        "██████████ ██████████ 12%",
        "████████████████████ 25%",
        "████████████████████████████ 38%",
        "██████████████████████████████████ 50%",
        "████████████████████████████████████████ 62%",
        "██████████████████████████████████████████████ 75%",
        "█████████████████████████████████████████████████████████ 88%",
        "███████████████████████████████████████████████████████████████████ 100%",
    ]
    phases = [
        "Locating target...",
        "Pinging IP...",
        "Bypassing firewall...",
        "Downloading data...",
        "Decrypting files...",
        "Extracting history...",
        "Accessing camera...",
        "Uploading to cloud...",
        "Finalising...",
    ]
    for i, (bar, phase) in enumerate(zip(bars, phases)):
        await asyncio.sleep(0.8)
        await msg.edit(content=f"`{bar}`\n📡 {phase}")
    await asyncio.sleep(0.6)
    await msg.edit(content=f"`███████████████████████████████████████████████████████████████████ 100%`\n✅ **Hack complete.** I now know everything about **{target}**. 😈")

@bot.tree.command(name="passwordgen", description="Generate a random strong password")
@app_commands.describe(length="Password length (8–32, default 16)")
async def passwordgen(interaction: discord.Interaction, length: int = 16):
    if length < 8 or length > 32:
        return await interaction.response.send_message("❌ Length must be 8–32.", ephemeral=True)
    import string
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    password = "".join(random.choice(chars) for _ in range(length))
    await interaction.response.send_message(f"🔑 Your password: `{password}`\n*(Keep this private!)*", ephemeral=True)

@bot.tree.command(name="ascii", description="Convert text into big ASCII art letters")
@app_commands.describe(text="Text to convert (keep it short!)")
async def ascii_art(interaction: discord.Interaction, text: str):
    if len(text) > 10:
        return await interaction.response.send_message("❌ Keep text under 10 characters.", ephemeral=True)
    blocks = {
        "a":"/-\\ |_|","b":"|_ |_","c":"/- \\-",
        "d":"|) |)","e":"|= |_","f":"|= |",
        "g":"/- /_|","h":"|_| |_|","i":"|_ |_",
        "j":"  | J/","k":"|/ |\\","l":"|  |_",
        "m":"|V| |V|","n":"|\\| |\\|","o":"/O\\ \\O/",
        "p":"|- |","q":"/O\\ \\_Q","r":"|- |<",
        "s":"/= =\\","t":"T  |","u":"| | \\_/",
        "v":"\\ / V","w":"W  VV","x":">\\ /<",
        "y":"\\ / |","z":"Z- /Z"
    }
    lines1, lines2 = [], []
    for c in text.lower():
        if c in blocks:
            p = blocks[c].split(" ")
            lines1.append(p[0].ljust(5))
            lines2.append(p[1].ljust(5) if len(p) > 1 else "     ")
        else:
            lines1.append(c.upper().ljust(5))
            lines2.append("     ")
    result = "".join(lines1) + "\n" + "".join(lines2)
    await interaction.response.send_message(f"```\n{result}\n```")

@bot.tree.command(name="decode", description="Decode text from various formats")
@app_commands.describe(text="Text to decode", style="Encoding format")
@app_commands.choices(style=[
    app_commands.Choice(name="Base64", value="base64"),
    app_commands.Choice(name="Binary", value="binary"),
])
async def decode(interaction: discord.Interaction, text: str, style: app_commands.Choice[str]):
    import base64
    try:
        if style.value == "base64":
            result = base64.b64decode(text.encode()).decode("utf-8")
        else:
            result = "".join(chr(int(b, 2)) for b in text.split())
        await interaction.response.send_message(f"🔓 Decoded: `{result}`")
    except Exception:
        await interaction.response.send_message("❌ Couldn't decode that. Make sure the format is correct.", ephemeral=True)

@bot.tree.command(name="encode", description="Encode text into various formats")
@app_commands.describe(text="Text to encode", style="Encoding format")
@app_commands.choices(style=[
    app_commands.Choice(name="Base64", value="base64"),
    app_commands.Choice(name="Binary", value="binary"),
])
async def encode(interaction: discord.Interaction, text: str, style: app_commands.Choice[str]):
    import base64
    if style.value == "base64":
        result = base64.b64encode(text.encode()).decode()
    else:
        result = " ".join(format(ord(c), "08b") for c in text)
    if len(result) > 1900:
        return await interaction.response.send_message("❌ Result too long to display.", ephemeral=True)
    await interaction.response.send_message(f"🔒 Encoded: `{result}`")

@bot.tree.command(name="typerace", description="Get a random sentence to copy as fast as you can!")
async def typerace(interaction: discord.Interaction):
    sentences = [
        "The quick brown fox jumps over the lazy dog.",
        "Pack my box with five dozen liquor jugs.",
        "How vexingly quick daft zebras jump.",
        "The five boxing wizards jump quickly.",
        "Sphinx of black quartz, judge my vow.",
        "Two driven jocks help fax my big quiz.",
        "The job requires extra pluck and zeal from every young wage earner.",
        "A wizard's job is to vex chumps quickly in fog.",
    ]
    sentence = random.choice(sentences)
    embed = discord.Embed(title="⌨️ Type Race!", color=discord.Color.green())
    embed.description = f"**Type this as fast as you can:**\n\n```{sentence}```"
    embed.set_footer(text="First one to type it correctly wins!")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="roll_stats", description="Roll D&D-style character stats (6x 4d6 drop lowest)")
async def roll_stats(interaction: discord.Interaction):
    stat_names = ["STR","DEX","CON","INT","WIS","CHA"]
    stats = []
    for _ in range(6):
        rolls = sorted([random.randint(1,6) for _ in range(4)])
        total = sum(rolls[1:])
        stats.append(total)
    embed = discord.Embed(title="🎲 Character Stats", color=discord.Color.purple())
    for name, val in zip(stat_names, stats):
        bar = "█" * (val // 2) + "░" * (10 - val // 2)
        embed.add_field(name=name, value=f"`{bar}` **{val}**", inline=True)
    embed.set_footer(text=f"Total: {sum(stats)} | Average: {sum(stats)//6}")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="love_letter", description="Generate a random love letter for someone")
@app_commands.describe(member="Who to send it to")
async def love_letter(interaction: discord.Interaction, member: discord.Member):
    openers = ["My dearest","Beloved","Darling","My sweet","O radiant"]
    middles = [
        "every moment I spend with you is a treasure I hold close to my heart.",
        "you make the world brighter just by existing in it.",
        "I'd cross a thousand servers just to ping you good morning.",
        "you are the reason my notifications are always on.",
        "your presence makes this Discord feel like home.",
    ]
    closers = ["Forever yours","With all my heart","Eternally","Your biggest admirer","Yours truly"]
    letter = f"{random.choice(openers)} {member.display_name},\n\n{random.choice(middles).capitalize()}\n\n{random.choice(closers)},\n{interaction.user.display_name} 💌"
    embed = discord.Embed(title="💌 Love Letter", description=letter, color=discord.Color.pink())
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="battle", description="Two members battle it out!")
@app_commands.describe(opponent="Who to battle")
async def battle(interaction: discord.Interaction, opponent: discord.Member):
    if opponent == interaction.user:
        return await interaction.response.send_message("❌ You can't battle yourself!", ephemeral=True)
    challenger = interaction.user
    moves = ["🗡️ landed a critical hit","🛡️ defended perfectly","💥 unleashed a combo","⚡ used a special attack","🎯 hit a weak spot","🌀 used confusion","🔥 used fire blast","❄️ used ice beam"]
    rounds = []
    hp_c, hp_o = 100, 100
    for i in range(3):
        move_c = random.choice(moves)
        move_o = random.choice(moves)
        dmg_c = random.randint(10, 35)
        dmg_o = random.randint(10, 35)
        hp_o -= dmg_c
        hp_c -= dmg_o
        rounds.append(f"**Round {i+1}:**\n⚔️ {challenger.display_name} {move_c} for **{dmg_c}** damage!\n⚔️ {opponent.display_name} {move_o} for **{dmg_o}** damage!")
    winner = challenger if hp_o < hp_c else opponent if hp_c < hp_o else None
    embed = discord.Embed(title=f"⚔️ {challenger.display_name} vs {opponent.display_name}", color=discord.Color.red())
    embed.description = "\n\n".join(rounds)
    if winner:
        embed.add_field(name="🏆 Winner", value=winner.mention, inline=False)
    else:
        embed.add_field(name="Result", value="It's a tie!", inline=False)
    await interaction.response.send_message(embed=embed)

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 8 — ROBLOX COMMANDS
# ═════════════════════════════════════════════════════════════════════════════

ROBLOX_CLIENT_ID     = os.getenv("ROBLOX_CLIENT_ID",     "YOUR_ROBLOX_CLIENT_ID_HERE")
ROBLOX_CLIENT_SECRET = os.getenv("ROBLOX_CLIENT_SECRET", "YOUR_ROBLOX_CLIENT_SECRET_HERE")
ROBLOX_GROUP_ID      = os.getenv("ROBLOX_GROUP_ID",      "YOUR_ROBLOX_GROUP_ID_HERE")
SESSION_SECRET       = os.getenv("SESSION_SECRET", "")
ROBLOX_TOKENS_FILE   = "roblox_tokens.json"

# Callback URL must be registered in your Roblox OAuth app settings.
# Prefer the live deploy URL from the environment; if absent, derive it from Replit/public URL.
ROBLOX_REDIRECT_URI = _get_roblox_redirect_uri()

async def _roblox_get(session: aiohttp.ClientSession, url: str, token: str | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as r:
        if r.status != 200:
            return {}
        return await r.json()

async def _roblox_post(session: aiohttp.ClientSession, url: str, json_body: dict, token: str | None = None) -> dict:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with session.post(url, json=json_body, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as r:
        try:
            return await r.json()
        except Exception:
            return {}

API_BASE = "http://localhost:8080/api"   # internal – same container as the bot

async def _load_roblox_token(discord_id: int) -> str | None:
    """Fetch the stored Roblox access token for a Discord user from the local link file."""
    try:
        data = _load_roblox_link(discord_id)
        return data.get("access_token") if data else None
    except Exception:
        return None

async def _ack_interaction(interaction: discord.Interaction, *, ephemeral: bool = False) -> None:
    if interaction.response.is_done():
        return
    try:
        await interaction.response.defer(ephemeral=ephemeral)
    except Exception:
        pass

async def _reply_interaction(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    ephemeral: bool = False,
) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content=content, embed=embed, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content=content, embed=embed, ephemeral=ephemeral)
    except Exception:
        try:
            await interaction.followup.send(content=content, embed=embed, ephemeral=ephemeral)
        except Exception:
            pass

# ── /roblox <username> — public profile lookup ────────────────────────────────
@bot.tree.command(name="roblox", description="Look up a Roblox user's profile and avatar")
@app_commands.describe(username="Roblox username to search")
async def roblox_lookup(interaction: discord.Interaction, username: str):
    await _ack_interaction(interaction)
    async with aiohttp.ClientSession() as session:
        # 1. Resolve username → user ID via POST endpoint
        async with session.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [username], "excludeBannedUsers": False},
            headers={"Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            data = await r.json() if r.status == 200 else {}

        results = data.get("data", [])
        if not results:
            await interaction.followup.send(f"❌ No Roblox user found for **{username}**.", ephemeral=True)
            return

        user = results[0]
        uid  = user["id"]

        # 2. Full profile
        profile = await _roblox_get(session, f"https://users.roblox.com/v1/users/{uid}")

        # 3. Avatar headshot thumbnail
        thumb_data = await _roblox_get(
            session,
            f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={uid}&size=420x420&format=Png&isCircular=false"
        )
        thumb_url = None
        thumbs = thumb_data.get("data", [])
        if thumbs and thumbs[0].get("state") == "Completed":
            thumb_url = thumbs[0]["imageUrl"]

        # 4. Friend / follower counts
        friends = await _roblox_get(session, f"https://friends.roblox.com/v1/users/{uid}/friends/count")
        followers = await _roblox_get(session, f"https://friends.roblox.com/v1/users/{uid}/followers/count")

    display  = profile.get("displayName", user.get("displayName", username))
    name     = profile.get("name", username)
    desc     = profile.get("description", "") or "No bio."
    if len(desc) > 200:
        desc = desc[:197] + "..."
    created  = profile.get("created", "")[:10] if profile.get("created") else "Unknown"
    banned   = profile.get("isBanned", False)

    embed = discord.Embed(
        title=f"{'🚫 ' if banned else ''}**{display}** (@{name})",
        url=f"https://www.roblox.com/users/{uid}/profile",
        description=desc,
        color=discord.Color.red() if banned else discord.Color.from_rgb(226, 35, 26),
    )
    embed.add_field(name="User ID",   value=str(uid),                        inline=True)
    embed.add_field(name="Joined",    value=created,                         inline=True)
    embed.add_field(name="Banned",    value="Yes ⛔" if banned else "No ✅",  inline=True)
    embed.add_field(name="Friends",   value=str(friends.get("count", "?")),  inline=True)
    embed.add_field(name="Followers", value=str(followers.get("count","?")), inline=True)
    if thumb_url:
        embed.set_thumbnail(url=thumb_url)
    embed.set_footer(text="Roblox • roblox.com")
    await interaction.followup.send(embed=embed)

# ── /robloxlink — start OAuth flow ────────────────────────────────────────────
@bot.tree.command(name="robloxlink", description="Link your Roblox account to this bot via Roblox OAuth")
async def roblox_link(interaction: discord.Interaction):
    if ROBLOX_CLIENT_ID.startswith("YOUR_") or ROBLOX_CLIENT_SECRET.startswith("YOUR_"):
        await interaction.response.send_message(
            "⚙️ Roblox OAuth is not configured yet. Ask the bot owner to set up `ROBLOX_CLIENT_ID` and `ROBLOX_CLIENT_SECRET`.",
            ephemeral=True,
        )
        return
    import urllib.parse
    state = str(interaction.user.id)
    if SESSION_SECRET:
        state = f"{interaction.user.id}:{SESSION_SECRET}"
    scopes  = "openid profile"
    params = urllib.parse.urlencode(
        [
            ("client_id", ROBLOX_CLIENT_ID),
            ("redirect_uri", ROBLOX_REDIRECT_URI),
            ("scope", scopes),
            ("response_type", "code"),
            ("state", state),
            ("prompt", "select_account consent"),
        ],
        doseq=True,
    )
    auth_url = f"https://apis.roblox.com/oauth/v1/authorize?{params}"
    embed = discord.Embed(
        title="🔗 Link Your Roblox Account",
        description=(
            f"Open the link below, approve the request, and return to Discord.\n\n"
            f"[**Authorize on Roblox →**]({auth_url})\n\n"
            f"Once you approve, the bot will store the token locally and you can use `/robloxme`, "
            f"`/robloxannounce`, and `/robloxrole`."
        ),
        color=discord.Color.from_rgb(226, 35, 26),
    )
    embed.set_footer(text="Your Roblox credentials are never stored — only the OAuth token.")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ── /robloxunlink — remove linked Roblox account ─────────────────────────────
@bot.tree.command(name="robloxunlink", description="Unlink your Roblox account from this bot")
async def roblox_unlink(interaction: discord.Interaction):
    await _ack_interaction(interaction, ephemeral=True)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.delete(
                f"{API_BASE}/roblox/linked/{interaction.user.id}",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                data = await r.json() if r.status == 200 else {}
        if data.get("unlinked"):
            await interaction.followup.send("✅ Your Roblox account has been unlinked.", ephemeral=True)
        else:
            await interaction.followup.send("ℹ️ No linked Roblox account found for your Discord account.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error unlinking: {e}", ephemeral=True)

# ── /robloxme — show your linked profile ─────────────────────────────────────
@bot.tree.command(name="robloxme", description="Show your linked Roblox profile")
async def roblox_me(interaction: discord.Interaction):
    await _ack_interaction(interaction, ephemeral=True)
    token = await _load_roblox_token(interaction.user.id)
    if not token:
        await interaction.followup.send(
            "❌ You haven't linked your Roblox account yet. Use `/robloxlink` first.", ephemeral=True
        )
        return
    async with aiohttp.ClientSession() as session:
        me = await _roblox_get(session, "https://apis.roblox.com/oauth/v1/userinfo", token=token)
    if not me:
        await interaction.followup.send("❌ Couldn't fetch your Roblox profile. Try `/robloxlink` again.", ephemeral=True)
        return
    uid      = me.get("sub", "?")
    name     = me.get("preferred_username", "Unknown")
    display  = me.get("name", name)
    pic      = me.get("picture", "")
    embed = discord.Embed(
        title=f"🎮 {display} (@{name})",
        url=f"https://www.roblox.com/users/{uid}/profile",
        color=discord.Color.from_rgb(226, 35, 26),
    )
    embed.add_field(name="Roblox ID", value=str(uid), inline=True)
    embed.add_field(name="Linked as", value=interaction.user.mention, inline=True)
    if pic:
        embed.set_thumbnail(url=pic)
    embed.set_footer(text="Linked via Roblox OAuth")
    await interaction.followup.send(embed=embed, ephemeral=True)

# ── /robloxannounce — post a group shout ─────────────────────────────────────
@bot.tree.command(name="robloxannounce", description="Post an announcement/shout to your Roblox group (owner only)")
@app_commands.describe(message="The announcement message to post")
@app_commands.check(is_owner)
async def roblox_announce(interaction: discord.Interaction, message: str):
    await _ack_interaction(interaction, ephemeral=True)
    token = await _load_roblox_token(interaction.user.id)
    if not token:
        await interaction.followup.send("❌ Link your Roblox account first with `/robloxlink`.", ephemeral=True)
        return
    if ROBLOX_GROUP_ID.startswith("YOUR_"):
        await interaction.followup.send("⚙️ `ROBLOX_GROUP_ID` is not configured yet.", ephemeral=True)
        return
    async with aiohttp.ClientSession() as session:
        result = await _roblox_post(
            session,
            f"https://groups.roblox.com/v1/groups/{ROBLOX_GROUP_ID}/status",
            {"message": message},
            token=token,
        )
    if result.get("body") == message or result.get("id"):
        await interaction.followup.send(f"✅ Group shout posted:\n> {message}", ephemeral=True)
    else:
        await interaction.followup.send(
            f"❌ Failed to post shout. Make sure you are the group owner and your account is linked.\n`{result}`",
            ephemeral=True,
        )

# ── /robloxrole — give a Roblox group role by username ───────────────────────
@bot.tree.command(name="robloxrole", description="Give a Roblox group role to a user by their Roblox username (owner only)")
@app_commands.describe(username="Roblox username of the target user", rolename="Exact name of the group role to assign")
@app_commands.check(is_owner)
async def roblox_role(interaction: discord.Interaction, username: str, rolename: str):
    await _ack_interaction(interaction, ephemeral=True)
    token = await _load_roblox_token(interaction.user.id)
    if not token:
        await interaction.followup.send("❌ Link your Roblox account first with `/robloxlink`.", ephemeral=True)
        return
    if ROBLOX_GROUP_ID.startswith("YOUR_"):
        await interaction.followup.send("⚙️ `ROBLOX_GROUP_ID` is not configured yet.", ephemeral=True)
        return

    async with aiohttp.ClientSession() as session:
        # Resolve username → user ID
        async with session.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [username]},
            headers={"Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            udata = await r.json() if r.status == 200 else {}
        users = udata.get("data", [])
        if not users:
            await interaction.followup.send(f"❌ Roblox user **{username}** not found.", ephemeral=True)
            return
        uid = users[0]["id"]

        # Get all group roles
        roles_data = await _roblox_get(
            session,
            f"https://groups.roblox.com/v1/groups/{ROBLOX_GROUP_ID}/roles",
        )
        roles = roles_data.get("roles", [])
        role  = next((r for r in roles if r["name"].lower() == rolename.lower()), None)
        if not role:
            names = ", ".join(r["name"] for r in roles)
            await interaction.followup.send(
                f"❌ Role **{rolename}** not found. Available roles: {names}", ephemeral=True
            )
            return
        role_id = role["id"]

        # Set the user's role
        result = await _roblox_post(
            session,
            f"https://groups.roblox.com/v1/groups/{ROBLOX_GROUP_ID}/users/{uid}",
            {"roleId": role_id},
            token=token,
        )

    if not result.get("errors"):
        await interaction.followup.send(
            f"✅ **{username}** has been given the **{rolename}** role in your Roblox group.", ephemeral=True
        )
    else:
        await interaction.followup.send(
            f"❌ Failed: `{result.get('errors', result)}`", ephemeral=True
        )

# ── /robloxroles — list all roles in the group ───────────────────────────────
@bot.tree.command(name="robloxroles", description="List all roles in your configured Roblox group")
async def roblox_roles(interaction: discord.Interaction):
    await _ack_interaction(interaction)
    if ROBLOX_GROUP_ID.startswith("YOUR_"):
        await interaction.followup.send("⚙️ `ROBLOX_GROUP_ID` is not configured yet.", ephemeral=True)
        return
    async with aiohttp.ClientSession() as session:
        roles_data = await _roblox_get(
            session,
            f"https://groups.roblox.com/v1/groups/{ROBLOX_GROUP_ID}/roles",
        )
    roles = roles_data.get("roles", [])
    if not roles:
        await interaction.followup.send("❌ Couldn't fetch group roles.", ephemeral=True)
        return
    embed = discord.Embed(
        title=f"🎮 Roblox Group Roles",
        color=discord.Color.from_rgb(226, 35, 26),
    )
    for r in sorted(roles, key=lambda x: x.get("rank", 0)):
        embed.add_field(
            name=f"Rank {r.get('rank','?')} — {r['name']}",
            value=f"Role ID: `{r['id']}` · Members: {r.get('memberCount', '?')}",
            inline=False,
        )
    embed.set_footer(text=f"Group ID: {ROBLOX_GROUP_ID}")
    await interaction.followup.send(embed=embed)

# ── /robloxgame <name> — search Roblox games ─────────────────────────────────
@bot.tree.command(name="robloxgame", description="Search for a Roblox game and show its info")
@app_commands.describe(name="Name of the Roblox game to search for")
async def roblox_game(interaction: discord.Interaction, name: str):
    await _ack_interaction(interaction)
    async with aiohttp.ClientSession() as session:
        # 1. Search for games
        search_url = (
            f"https://games.roblox.com/v1/games/list"
            f"?model.keyword={name.replace(' ', '+')}"
            f"&model.maxRows=6&model.startRows=0"
            f"&model.includeNotAllGames=false"
        )
        async with session.get(search_url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            sdata = await r.json() if r.status == 200 else {}

        games = sdata.get("games", [])
        if not games:
            await interaction.followup.send(f"❌ No Roblox games found for **{name}**.", ephemeral=True)
            return

        game = games[0]
        universe_id = game.get("universeId") or game.get("id")
        place_id    = game.get("placeId") or game.get("rootPlaceId")
        title       = game.get("name", "Unknown")
        creator     = game.get("creatorName", "Unknown")
        players     = game.get("playerCount", 0)
        visits      = game.get("totalUpVotes", 0)   # fallback
        likes       = game.get("totalUpVotes", 0)
        dislikes    = game.get("totalDownVotes", 0)

        # 2. Get full game details for visits / description
        detail_url = f"https://games.roblox.com/v1/games?universeIds={universe_id}"
        async with session.get(detail_url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            ddata = await r.json() if r.status == 200 else {}
        detail = (ddata.get("data") or [{}])[0]
        visits      = detail.get("visits", visits)
        description = (detail.get("description") or "No description.")[:200]
        max_players = detail.get("maxPlayers", "?")
        genre       = detail.get("genre", "?")

        # 3. Get game thumbnail
        thumb_url = None
        if universe_id:
            async with session.get(
                f"https://thumbnails.roblox.com/v1/games/multiget/thumbnails"
                f"?universeIds={universe_id}&size=768x432&format=Png&isCircular=false",
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                tdata = await r.json() if r.status == 200 else {}
            thumbs = (tdata.get("data") or [{}])[0].get("thumbnails", [])
            if thumbs and thumbs[0].get("state") == "Completed":
                thumb_url = thumbs[0]["imageUrl"]

    play_url = f"https://www.roblox.com/games/{place_id}" if place_id else f"https://www.roblox.com"

    def _fmt(n: int) -> str:
        if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
        if n >= 1_000:     return f"{n/1_000:.1f}K"
        return str(n)

    embed = discord.Embed(
        title=title,
        url=play_url,
        description=description,
        color=discord.Color.from_rgb(226, 35, 26),
    )
    embed.set_author(name=f"By {creator}")
    embed.add_field(name="🟢 Playing",    value=str(players),      inline=True)
    embed.add_field(name="👁️ Visits",     value=_fmt(visits),      inline=True)
    embed.add_field(name="👥 Max Players",value=str(max_players),  inline=True)
    embed.add_field(name="🎮 Genre",      value=genre,             inline=True)
    embed.add_field(name="👍 Likes",      value=_fmt(likes),       inline=True)
    embed.add_field(name="👎 Dislikes",   value=_fmt(dislikes),    inline=True)
    embed.add_field(name="▶️ Play Now",   value=f"[Open in Roblox]({play_url})", inline=False)
    if thumb_url:
        embed.set_image(url=thumb_url)
    embed.set_footer(text="Roblox • roblox.com")
    await interaction.followup.send(embed=embed)

# ── /robloxstatus — live Roblox service health ────────────────────────────────
@bot.tree.command(name="robloxstatus", description="Check Roblox's live service status")
async def roblox_status(interaction: discord.Interaction):
    await _ack_interaction(interaction)
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://status.roblox.com/api/v2/summary.json",
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            data = await r.json() if r.status == 200 else {}

    if not data:
        await interaction.followup.send("❌ Could not reach the Roblox status page.", ephemeral=True)
        return

    indicator = data.get("status", {}).get("indicator", "none")
    overall   = data.get("status", {}).get("description", "Unknown")
    color_map = {"none": discord.Color.green(), "minor": discord.Color.yellow(),
                 "major": discord.Color.orange(), "critical": discord.Color.red()}
    icon_map  = {"none": "✅", "minor": "⚠️", "major": "🔶", "critical": "🔴"}

    embed = discord.Embed(
        title=f"{icon_map.get(indicator, '❓')} Roblox Status — {overall}",
        url="https://status.roblox.com",
        color=color_map.get(indicator, discord.Color.greyple()),
    )
    for comp in data.get("components", [])[:12]:
        status = comp.get("status", "unknown").replace("_", " ").title()
        icon   = "✅" if comp["status"] == "operational" else "⚠️" if "degraded" in comp["status"] else "🔴"
        embed.add_field(name=comp.get("name", "?"), value=f"{icon} {status}", inline=True)
    incidents = data.get("incidents", [])
    if incidents:
        inc = incidents[0]
        embed.add_field(
            name="🚨 Active Incident",
            value=f"**{inc.get('name','?')}**\n{inc.get('shortlink','')}",
            inline=False,
        )
    embed.set_footer(text="status.roblox.com")
    await interaction.followup.send(embed=embed)

# ── /robloxavatar <username> — full body avatar render ────────────────────────
@bot.tree.command(name="robloxavatar", description="Show a Roblox user's full body avatar")
@app_commands.describe(username="Roblox username")
async def roblox_avatar(interaction: discord.Interaction, username: str):
    await _ack_interaction(interaction)
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [username], "excludeBannedUsers": False},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            udata = await r.json() if r.status == 200 else {}
        users = udata.get("data", [])
        if not users:
            await interaction.followup.send(f"❌ User **{username}** not found.", ephemeral=True)
            return
        uid = users[0]["id"]
        display = users[0].get("displayName", username)

        # Full body
        async with session.get(
            f"https://thumbnails.roblox.com/v1/users/avatar"
            f"?userIds={uid}&size=420x420&format=Png&isCircular=false",
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            tdata = await r.json() if r.status == 200 else {}
        full_url = None
        for entry in tdata.get("data", []):
            if entry.get("state") == "Completed":
                full_url = entry["imageUrl"]
                break

        # 3D avatar render (bust)
        async with session.get(
            f"https://thumbnails.roblox.com/v1/users/avatar-bust"
            f"?userIds={uid}&size=420x420&format=Png&isCircular=false",
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            bdata = await r.json() if r.status == 200 else {}
        bust_url = None
        for entry in bdata.get("data", []):
            if entry.get("state") == "Completed":
                bust_url = entry["imageUrl"]
                break

    embed = discord.Embed(
        title=f"{display}'s Avatar",
        url=f"https://www.roblox.com/users/{uid}/profile",
        color=discord.Color.from_rgb(226, 35, 26),
    )
    if full_url:
        embed.set_image(url=full_url)
    if bust_url:
        embed.set_thumbnail(url=bust_url)
    embed.set_footer(text=f"User ID: {uid}")
    await interaction.followup.send(embed=embed)

# ── /robloxbadges <username> — recent badges ─────────────────────────────────
@bot.tree.command(name="robloxbadges", description="Show a Roblox user's recently earned badges")
@app_commands.describe(username="Roblox username")
async def roblox_badges(interaction: discord.Interaction, username: str):
    await _ack_interaction(interaction)
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [username], "excludeBannedUsers": False},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            udata = await r.json() if r.status == 200 else {}
        users = udata.get("data", [])
        if not users:
            await interaction.followup.send(f"❌ User **{username}** not found.", ephemeral=True)
            return
        uid = users[0]["id"]
        display = users[0].get("displayName", username)

        async with session.get(
            f"https://badges.roblox.com/v1/users/{uid}/badges?limit=10&sortOrder=Desc",
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            bdata = await r.json() if r.status == 200 else {}

    badges = bdata.get("data", [])
    embed = discord.Embed(
        title=f"🏅 {display}'s Recent Badges",
        url=f"https://www.roblox.com/users/{uid}/profile",
        color=discord.Color.gold(),
        description=f"Showing up to {len(badges)} recently earned badges." if badges else "No badges found.",
    )
    for b in badges[:10]:
        game = b.get("awardingUniverse", {})
        game_name = game.get("name", "Unknown Game") if game else "Unknown Game"
        embed.add_field(
            name=b.get("name", "?"),
            value=f"*{b.get('description','')[:60] or 'No description'}*\n🎮 {game_name}",
            inline=False,
        )
    embed.set_footer(text=f"User ID: {uid}")
    await interaction.followup.send(embed=embed)

# ── /robloxfriends <username> — friends list ─────────────────────────────────
@bot.tree.command(name="robloxfriends", description="Show a Roblox user's friends")
@app_commands.describe(username="Roblox username")
async def roblox_friends(interaction: discord.Interaction, username: str):
    await _ack_interaction(interaction)
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [username], "excludeBannedUsers": False},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            udata = await r.json() if r.status == 200 else {}
        users = udata.get("data", [])
        if not users:
            await interaction.followup.send(f"❌ User **{username}** not found.", ephemeral=True)
            return
        uid = users[0]["id"]
        display = users[0].get("displayName", username)

        async with session.get(
            f"https://friends.roblox.com/v1/users/{uid}/friends/count",
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            cdata = await r.json() if r.status == 200 else {}
        count = cdata.get("count", 0)

        async with session.get(
            f"https://friends.roblox.com/v1/users/{uid}/friends?limit=200",
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            fdata = await r.json() if r.status == 200 else {}

    friends = fdata.get("data", [])[:15]
    embed = discord.Embed(
        title=f"👥 {display}'s Friends ({count} total)",
        url=f"https://www.roblox.com/users/{uid}/profile",
        color=discord.Color.blue(),
    )
    if friends:
        names = [f"[{f.get('displayName', f['name'])}](https://www.roblox.com/users/{f['id']}/profile)" for f in friends]
        embed.description = " • ".join(names)
    else:
        embed.description = "No friends found or profile is private."
    embed.set_footer(text=f"User ID: {uid}")
    await interaction.followup.send(embed=embed)

# ── /robloxgroup <name> — group lookup ───────────────────────────────────────
@bot.tree.command(name="robloxgroup", description="Look up a Roblox group by name")
@app_commands.describe(name="Group name to search")
async def roblox_group_lookup(interaction: discord.Interaction, name: str):
    await _ack_interaction(interaction)
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://groups.roblox.com/v1/groups/search?keyword={name.replace(' ', '+')}&prioritizeExactMatch=true&limit=10",
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            sdata = await r.json() if r.status == 200 else {}

        results = sdata.get("data", [])
        if not results:
            await interaction.followup.send(f"❌ No groups found for **{name}**.", ephemeral=True)
            return
        g = results[0]
        gid = g.get("id")

        async with session.get(
            f"https://groups.roblox.com/v1/groups/{gid}",
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            gdata = await r.json() if r.status == 200 else g

        # Group icon
        async with session.get(
            f"https://thumbnails.roblox.com/v1/groups/icons?groupIds={gid}&size=420x420&format=Png",
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            idata = await r.json() if r.status == 200 else {}
        icon_url = None
        for entry in idata.get("data", []):
            if entry.get("state") == "Completed":
                icon_url = entry["imageUrl"]
                break

    owner     = gdata.get("owner") or {}
    desc      = (gdata.get("description") or "No description.")[:300]
    members   = gdata.get("memberCount", 0)
    is_public = gdata.get("publicEntryAllowed", True)
    verified  = gdata.get("hasVerifiedBadge", False)

    embed = discord.Embed(
        title=f"{gdata.get('name', name)} {'✅' if verified else ''}",
        url=f"https://www.roblox.com/groups/{gid}",
        description=desc,
        color=discord.Color.from_rgb(226, 35, 26),
    )
    embed.add_field(name="👥 Members",    value=f"{members:,}",                          inline=True)
    embed.add_field(name="👑 Owner",      value=owner.get("displayName", "None") if owner else "None", inline=True)
    embed.add_field(name="🔓 Entry",      value="Open" if is_public else "Closed/Approval", inline=True)
    if icon_url:
        embed.set_thumbnail(url=icon_url)
    embed.set_footer(text=f"Group ID: {gid}")
    await interaction.followup.send(embed=embed)

# ── /robloxpresence <username> — what is a user doing right now ───────────────
@bot.tree.command(name="robloxpresence", description="Check what a Roblox user is currently doing")
@app_commands.describe(username="Roblox username")
async def roblox_presence(interaction: discord.Interaction, username: str):
    await _ack_interaction(interaction)
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [username], "excludeBannedUsers": False},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            udata = await r.json() if r.status == 200 else {}
        users = udata.get("data", [])
        if not users:
            await interaction.followup.send(f"❌ User **{username}** not found.", ephemeral=True)
            return
        uid = users[0]["id"]
        display = users[0].get("displayName", username)

        async with session.post(
            "https://presence.roblox.com/v1/presence/users",
            json={"userIds": [uid]},
            headers={"Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            pdata = await r.json() if r.status == 200 else {}

        # Headshot
        async with session.get(
            f"https://thumbnails.roblox.com/v1/users/avatar-headshot"
            f"?userIds={uid}&size=420x420&format=Png&isCircular=true",
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            hdata = await r.json() if r.status == 200 else {}
        headshot = None
        for entry in hdata.get("data", []):
            if entry.get("state") == "Completed":
                headshot = entry["imageUrl"]
                break

    presences = pdata.get("userPresences", [{}])
    p = presences[0] if presences else {}
    ptype = p.get("userPresenceType", 0)
    status_map = {
        0: ("⚫ Offline",    discord.Color.dark_gray()),
        1: ("🟢 Online",     discord.Color.green()),
        2: ("🎮 In a Game",  discord.Color.from_rgb(226, 35, 26)),
        3: ("🏗️ In Studio",  discord.Color.blurple()),
    }
    status_str, color = status_map.get(ptype, ("❓ Unknown", discord.Color.greyple()))

    embed = discord.Embed(
        title=f"{display}'s Presence",
        url=f"https://www.roblox.com/users/{uid}/profile",
        color=color,
    )
    embed.add_field(name="Status", value=status_str, inline=True)
    if ptype == 2:
        embed.add_field(name="🎮 Game", value=p.get("lastLocation", "Unknown"), inline=True)
        place_id = p.get("placeId")
        if place_id:
            embed.add_field(name="▶️ Play", value=f"[Join Game](https://www.roblox.com/games/{place_id})", inline=True)
    last_online = p.get("lastOnline", "")
    if last_online:
        embed.add_field(name="🕐 Last Seen", value=last_online[:19].replace("T", " ") + " UTC", inline=False)
    if headshot:
        embed.set_thumbnail(url=headshot)
    embed.set_footer(text=f"User ID: {uid}")
    await interaction.followup.send(embed=embed)

# ── /robloxitem <name> — catalog item search ──────────────────────────────────
@bot.tree.command(name="robloxitem", description="Search the Roblox catalog for an item")
@app_commands.describe(name="Item name to search")
async def roblox_item(interaction: discord.Interaction, name: str):
    await _ack_interaction(interaction)
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://catalog.roblox.com/v1/search/items?category=All&keyword={name.replace(' ', '+')}&limit=10&salesTypeFilter=1",
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            sdata = await r.json() if r.status == 200 else {}

        items = sdata.get("data", [])
        if not items:
            await interaction.followup.send(f"❌ No catalog items found for **{name}**.", ephemeral=True)
            return
        item = items[0]
        item_id  = item.get("id")
        item_type = item.get("itemType", "Asset")  # "Asset" or "Bundle"

        # Get details
        endpoint = "assets" if item_type == "Asset" else "bundles"
        async with session.get(
            f"https://economy.roblox.com/v2/{endpoint}/{item_id}/details",
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            ddata = await r.json() if r.status == 200 else {}

        # Thumbnail
        async with session.get(
            f"https://thumbnails.roblox.com/v1/assets?assetIds={item_id}&size=420x420&format=Png",
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            tdata = await r.json() if r.status == 200 else {}
        thumb = None
        for entry in tdata.get("data", []):
            if entry.get("state") == "Completed":
                thumb = entry["imageUrl"]
                break

    creator   = ddata.get("Creator", {})
    price     = ddata.get("PriceInRobux")
    is_limited = ddata.get("IsLimited", False) or ddata.get("IsLimitedUnique", False)
    remaining  = ddata.get("Remaining")
    sales      = ddata.get("Sales", 0)
    desc       = (ddata.get("Description") or "No description.")[:200]
    asset_type = ddata.get("AssetTypeName", item_type)

    embed = discord.Embed(
        title=ddata.get("Name", name),
        url=f"https://www.roblox.com/catalog/{item_id}",
        description=desc,
        color=discord.Color.from_rgb(226, 35, 26),
    )
    embed.add_field(name="🏷️ Type",    value=asset_type,                                      inline=True)
    embed.add_field(name="💰 Price",   value=f"R${price:,}" if price else "Free / Off-sale", inline=True)
    embed.add_field(name="🔨 Creator", value=creator.get("Name", "Unknown"),                  inline=True)
    embed.add_field(name="🛒 Sales",   value=f"{sales:,}",                                    inline=True)
    if is_limited:
        embed.add_field(name="⚡ Limited", value=f"Remaining: {remaining}" if remaining is not None else "Yes", inline=True)
    if thumb:
        embed.set_thumbnail(url=thumb)
    embed.set_footer(text=f"Item ID: {item_id}")
    await interaction.followup.send(embed=embed)

# ── /robloxgroups <username> — groups a user belongs to ──────────────────────
@bot.tree.command(name="robloxgroups", description="List the Roblox groups a user is in")
@app_commands.describe(username="Roblox username")
async def roblox_user_groups(interaction: discord.Interaction, username: str):
    await _ack_interaction(interaction)
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [username], "excludeBannedUsers": False},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            udata = await r.json() if r.status == 200 else {}
        users = udata.get("data", [])
        if not users:
            await interaction.followup.send(f"❌ User **{username}** not found.", ephemeral=True)
            return
        uid = users[0]["id"]
        display = users[0].get("displayName", username)

        async with session.get(
            f"https://groups.roblox.com/v2/users/{uid}/groups/roles?includeLocked=false",
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            gdata = await r.json() if r.status == 200 else {}

    groups = gdata.get("data", [])
    embed = discord.Embed(
        title=f"🏘️ {display}'s Groups ({len(groups)} total)",
        url=f"https://www.roblox.com/users/{uid}/profile",
        color=discord.Color.from_rgb(226, 35, 26),
    )
    if not groups:
        embed.description = "This user is not in any groups, or their groups are private."
    else:
        for entry in groups[:15]:
            g    = entry.get("group", {})
            role = entry.get("role", {})
            gname = g.get("name", "?")
            gid   = g.get("id", 0)
            rname = role.get("name", "Member")
            embed.add_field(
                name=f"[{gname}](https://www.roblox.com/groups/{gid})",
                value=f"Role: **{rname}** • {g.get('memberCount', 0):,} members",
                inline=False,
            )
    embed.set_footer(text=f"User ID: {uid}")
    await interaction.followup.send(embed=embed)

# ── /robloxrap <username> — collectibles & RAP ────────────────────────────────
@bot.tree.command(name="robloxrap", description="Show a Roblox user's limited items and total RAP")
@app_commands.describe(username="Roblox username")
async def roblox_rap(interaction: discord.Interaction, username: str):
    await _ack_interaction(interaction)
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [username], "excludeBannedUsers": False},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            udata = await r.json() if r.status == 200 else {}
        users = udata.get("data", [])
        if not users:
            await interaction.followup.send(f"❌ User **{username}** not found.", ephemeral=True)
            return
        uid = users[0]["id"]
        display = users[0].get("displayName", username)

        async with session.get(
            f"https://inventory.roblox.com/v1/users/{uid}/assets/collectibles"
            f"?sortOrder=Desc&limit=100",
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            cdata = await r.json() if r.status == 200 else {}

    items = cdata.get("data", [])
    if not items:
        await interaction.followup.send(
            f"❌ **{display}** has no collectibles, or their inventory is private.", ephemeral=True
        )
        return

    total_rap = sum(i.get("recentAveragePrice", 0) for i in items)
    embed = discord.Embed(
        title=f"💎 {display}'s Limiteds — R${total_rap:,} RAP",
        url=f"https://www.roblox.com/users/{uid}/profile",
        color=discord.Color.gold(),
    )
    for item in items[:10]:
        rap  = item.get("recentAveragePrice", 0)
        name = item.get("name", "?")
        embed.add_field(
            name=name,
            value=f"💰 RAP: R${rap:,}",
            inline=True,
        )
    if len(items) > 10:
        embed.set_footer(text=f"Showing 10 of {len(items)} limiteds • User ID: {uid}")
    else:
        embed.set_footer(text=f"{len(items)} limiteds • User ID: {uid}")
    await interaction.followup.send(embed=embed)

# ── /robloxserver <game name> — active game servers ───────────────────────────
@bot.tree.command(name="robloxserver", description="Show active servers for a Roblox game")
@app_commands.describe(game="Roblox game name to search")
async def roblox_server(interaction: discord.Interaction, game: str):
    await _ack_interaction(interaction)
    async with aiohttp.ClientSession() as session:
        # Search for the game first
        async with session.get(
            f"https://games.roblox.com/v1/games/list?model.keyword={game.replace(' ', '+')}&model.maxRows=1",
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            sdata = await r.json() if r.status == 200 else {}

        games = sdata.get("games", [])
        if not games:
            await interaction.followup.send(f"❌ No game found for **{game}**.", ephemeral=True)
            return

        g         = games[0]
        place_id  = g.get("placeId") or g.get("rootPlaceId")
        game_name = g.get("name", game)
        players   = g.get("playerCount", 0)

        # Get public server list
        async with session.get(
            f"https://games.roblox.com/v1/games/{place_id}/servers/Public?sortOrder=Desc&limit=10",
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            svdata = await r.json() if r.status == 200 else {}

    servers = svdata.get("data", [])
    embed = discord.Embed(
        title=f"🖥️ {game_name} — Active Servers",
        url=f"https://www.roblox.com/games/{place_id}",
        color=discord.Color.from_rgb(226, 35, 26),
        description=f"**{players:,}** players online across all servers.",
    )
    if not servers:
        embed.add_field(name="No public servers found", value="The game may have no active servers or servers are private.", inline=False)
    else:
        for i, sv in enumerate(servers[:8], 1):
            playing = sv.get("playing", 0)
            maximum = sv.get("maxPlayers", 0)
            ping    = sv.get("ping", 0)
            fps     = round(sv.get("fps", 0), 1)
            bar_filled = int((playing / maximum * 8)) if maximum else 0
            bar = "█" * bar_filled + "░" * (8 - bar_filled)
            embed.add_field(
                name=f"Server #{i}",
                value=f"`{bar}` {playing}/{maximum}\n📶 {ping}ms • {fps} FPS",
                inline=True,
            )
    embed.set_footer(text=f"Place ID: {place_id}")
    await interaction.followup.send(embed=embed)

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 9 — CUSTOM COMMANDS & AUTO-MODERATION
# ═════════════════════════════════════════════════════════════════════════════

import re
import time as _time

URL_PATTERN = re.compile(r"https?://\S+|discord\.gg/\S+", re.IGNORECASE)

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        await bot.process_commands(message)
        return

    gid_str  = str(message.guild.id)
    all_automod = load_json(AUTOMOD_FILE) or {}
    automod  = all_automod.get(gid_str, {})
    content  = message.content

    # ── Word filter ──────────────────────────────────────────────────────────
    wf = automod.get("word_filter", {})
    if wf.get("enabled"):
        lower = content.lower()
        for word in wf.get("words", []):
            if word.lower() in lower:
                try:
                    await message.delete()
                    await message.channel.send(
                        f"⚠️ {message.author.mention} — your message was removed (word filter).",
                        delete_after=5,
                    )
                except discord.Forbidden:
                    pass
                return

    # ── Anti-link ────────────────────────────────────────────────────────────
    al = automod.get("anti_link", {})
    if al.get("enabled") and URL_PATTERN.search(content):
        try:
            await message.delete()
            await message.channel.send(
                f"⚠️ {message.author.mention} — links are not allowed here.",
                delete_after=5,
            )
        except discord.Forbidden:
            pass
        return

    # ── Anti-spam ────────────────────────────────────────────────────────────
    asp = automod.get("anti_spam", {})
    if asp.get("enabled"):
        gid  = message.guild.id
        uid  = message.author.id
        now  = _time.time()
        win  = asp.get("window_seconds", 5)
        limit = asp.get("max_messages", 5)
        spam_tracker.setdefault(gid, {}).setdefault(uid, [])
        spam_tracker[gid][uid] = [t for t in spam_tracker[gid][uid] if now - t < win]
        spam_tracker[gid][uid].append(now)
        if len(spam_tracker[gid][uid]) > limit:
            try:
                await message.delete()
                timeout_until = discord.utils.utcnow() + datetime.timedelta(seconds=30)
                await message.author.timeout(timeout_until, reason="Auto-mod: spamming")
                await message.channel.send(
                    f"⚠️ {message.author.mention} has been timed out for spamming.",
                    delete_after=8,
                )
                spam_tracker[gid][uid] = []
            except discord.Forbidden:
                pass
            return

    # ── Custom prefix commands (!name) ───────────────────────────────────────
    if content.startswith("!"):
        trigger = content[1:].split()[0].lower()
        all_cmds = load_json(CUSTOM_CMDS_FILE) or {}
        cmds = all_cmds.get(gid_str, {})
        if trigger in cmds:
            cmd = cmds[trigger]
            if cmd.get("owner_only"):
                if not is_owner(type("_", (), {"user": message.author, "guild": message.guild})()):
                    await message.channel.send("❌ Only the server owner can use this command.", delete_after=5)
                    return
            await message.channel.send(cmd["response"])
            return

    await bot.process_commands(message)

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 9 — ERROR HANDLER
# ═════════════════════════════════════════════════════════════════════════════

@bot.event
async def on_interaction(interaction: discord.Interaction):
    """Handle custom slash commands registered via the dashboard.
    Must always call tree.call_scheduled_callbacks to process built-in commands."""
    if (
        interaction.type == discord.InteractionType.application_command
        and not interaction.response.is_done()
    ):
        data = interaction.data or {}
        cmd_name = data.get("name", "")
        gid_str  = str(interaction.guild_id) if interaction.guild_id else ""
        if cmd_name and gid_str:
            all_cmds = load_json(CUSTOM_CMDS_FILE) or {}
            cmds = all_cmds.get(gid_str, {})
            if cmd_name in cmds:
                cmd = cmds[cmd_name]
                if cmd.get("owner_only") and not is_owner(interaction):
                    await interaction.response.send_message(
                        "❌ Only the server owner can use this command.", ephemeral=True
                    )
                    return
                await interaction.response.send_message(cmd["response"])
                return  # handled — tree has nothing scheduled for this command

    # Built-in slash commands are processed automatically by the tree
    # before on_interaction fires — no extra call needed here.

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    raise error

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        await _reply_interaction(interaction, "❌ Only the server owner can use this command.", ephemeral=True)
    else:
        await _reply_interaction(interaction, f"❌ An error occurred: {error}", ephemeral=True)

# ═════════════════════════════════════════════════════════════════════════════
# RUN
# ═════════════════════════════════════════════════════════════════════════════

token = os.environ.get("DISCORD_TOKEN")
if not token:
    raise ValueError("DISCORD_TOKEN is not set. Add it to your secrets.")

bot.run(token)
