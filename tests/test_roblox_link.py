import os
from threading import Thread

# 1. Force production environment settings
os.environ["BOT_TEST_MODE"] = "false"

# 2. Safely load your Discord Token if it isn't already set in Replit Secrets
if not os.environ.get("DISCORD_TOKEN"):
    # Replace 'YOUR_ACTUAL_DISCORD_TOKEN_HERE' if not using Replit Secrets
    os.environ["DISCORD_TOKEN"] = "MTUwNzc3NjUyNDE4OTgyNzI0Mg.GK2n1b.qPpRNWgo1_AuL-m5l__CtD5YEefaPge1NgBtSw"

# 3. Force your specific Roblox redirect URL into the environment
os.environ["ROBLOX_REDIRECT_URI"] = "https://discord-bot-script--jaxonmarshall98.replit.app/api/roblox/callback"

# 4. Import your main bot file to load the setup
import bot

# 5. Core execution logic to launch everything
if __name__ == "__main__":
    print("🚀 Starting the live Flask web server backend...")
    # Ensures the background web server handles the Roblox OAuth callbacks
    if hasattr(bot, 'app'):
        port = int(os.environ.get("PORT", 10000))
        Thread(target=lambda: bot.app.run(host="0.0.0.0", port=port), daemon=True).start()
        print(f"🌐 Web server actively listening on port {port}")

    print("🤖 Launching your live Discord Bot client...")
    # Retrieves the token and starts your actual Discord bot connection
    token = os.environ.get("DISCORD_TOKEN")
    if token and token != "YOUR_ACTUAL_DISCORD_TOKEN_HERE":
        bot.bot.run(token)
    else:
        print("❌ Error: Please set your real DISCORD_TOKEN in Replit Secrets!")
