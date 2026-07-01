import os
import discord
from discord.ext import commands
from keep_alive import keep_alive  # Import your web server

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

# Start the web server first
keep_alive()

# Start the bot using your GitHub secret / environment variable
bot.run(os.environ.get("DISCORD_TOKEN"))
