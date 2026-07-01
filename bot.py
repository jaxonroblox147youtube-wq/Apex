import os
import sys
import discord
from discord.ext import commands
from keep_alive import keep_alive

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        print("Starting slash command sync...")
        synced = await bot.tree.sync()
        print(f"Success! Synced {len(synced)} slash commands globally.")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")

# Start the web server first
try:
    print("Starting the Flask keep-alive server...")
    keep_alive()
except Exception as e:
    print(f"Failed to start Flask: {e}")

# Attempt to launch the bot
token = os.environ.get("DISCORD_TOKEN")
if not token:
    print("ERROR: DISCORD_TOKEN environment variable is completely empty!")
    sys.exit(1)

try:
    print("Attempting to connect to Discord...")
    bot.run(token)
except Exception as e:
    print(f"CRITICAL ERROR: Discord bot crashed on startup! Details: {e}")
    sys.exit(1)