import asyncio

async def _exchange_roblox_code(code: str, redirect_uri: str) -> dict:
    if ROBLOX_CLIENT_ID.startswith("YOUR_") or ROBLOX_CLIENT_SECRET.startswith("YOUR_"):
        return {}
    
    payload = {
        "client_id": ROBLOX_CLIENT_ID,
        "client_secret": ROBLOX_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        # FIXED: Set to your exact string specification
        "redirect_uri": "https://discord-bot-script--jaxonmarshall98.replit.app/api/roblox/callback",
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
def roblox_callback():
    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")
    
    if error:
        return f"<h1>Roblox link failed</h1><p>{error}</p>", 400
    if not code or not state:
        return "<h1>Roblox link failed</h1><p>Missing authorization data.</p>", 400
        
    try:
        # FIXED: Grabs the item from the split array before conversion
        discord_id_raw = state.split(":", 1)[0]
        discord_id = int(discord_id_raw)
    except (ValueError, IndexError):
        return "<h1>Roblox link failed</h1><p>Invalid Discord user state.</p>", 400

    # FIXED: Hardcoded your exact URL variant for the execution loop
    real_redirect = "https://discord-bot-script--jaxonmarshall98.replit.app/api/roblox/callback"
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        token_data = loop.run_until_complete(_exchange_roblox_code(code, real_redirect))
    except Exception:
        token_data = {}
    finally:
        loop.close()

    if not token_data or not token_data.get("access_token"):
        return "<h1>Roblox link failed</h1><p>The bot could not exchange the authorization code with Roblox.</p>", 400

    _store_roblox_link(discord_id, token_data)
    return "<h1>✅ Roblox account linked</h1><p>You can now use the Roblox commands in Discord.</p>"


def _start_flask_server() -> None:
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))


if os.environ.get("BOT_TEST_MODE", "").lower() not in {"1", "true", "yes", "on"}:
    Thread(target=_start_flask_server, daemon=True).start()
