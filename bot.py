import os
import asyncio
import logging
import aiohttp
from datetime import datetime, timedelta
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# ---------- SOZLAMALAR ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== API KALITI TOʻGʻRIDAN-TOʻGʻRI KOD ICHIDA ==========
API_KEY = "d1de28fade2e0d5ec98b956b46858df7"   # Sizning dashboard kalitingiz
API_HOST = "v3.football.api-sports.io"

# Top 5 chempionat (ID, nom)
TOP_LEAGUES = {
    39: {"name": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premyer Liga", "country": "Angliya"},
    140: {"name": "🇪🇸 La Liga", "country": "Ispaniya"},
    135: {"name": "🇮🇹 Seriya A", "country": "Italiya"},
    78: {"name": "🇩🇪 Bundesliga", "country": "Germaniya"},
    61: {"name": "🇫🇷 Liga 1", "country": "Fransiya"}
}

def get_current_season():
    """2025/2026 mavsumi uchun 2025 qaytaradi"""
    now = datetime.now()
    return now.year if now.month >= 8 else now.year - 1

# ---------- INLINE TUGMALAR ----------
def get_leagues_keyboard():
    keyboard = []
    for lid, data in TOP_LEAGUES.items():
        keyboard.append([InlineKeyboardButton(data["name"], callback_data=f"league_{lid}")])
    return InlineKeyboardMarkup(keyboard)

# ---------- STATIK MAʼLUMOT (API BOʻSH BOʻLSA) ----------
def get_static_matches(league_name):
    now = datetime.now()
    today = now.strftime("%d.%m")
    tomorrow = (now + timedelta(days=1)).strftime("%d.%m")
    day3 = (now + timedelta(days=2)).strftime("%d.%m")
    
    if "Premyer" in league_name:
        return [
            {"home": "Manchester City", "away": "Arsenal", "time": f"{today} 19:45"},
            {"home": "Liverpool", "away": "Chelsea", "time": f"{tomorrow} 21:00"},
            {"home": "Manchester United", "away": "Tottenham", "time": f"{day3} 18:30"}
        ]
    elif "La Liga" in league_name:
        return [
            {"home": "Real Madrid", "away": "Barcelona", "time": f"{tomorrow} 21:00"},
            {"home": "Atletico Madrid", "away": "Sevilla", "time": f"{today} 20:00"}
        ]
    elif "Seriya A" in league_name:
        return [
            {"home": "Inter", "away": "Juventus", "time": f"{tomorrow} 20:45"},
            {"home": "Milan", "away": "Napoli", "time": f"{today} 19:30"}
        ]
    elif "Bundesliga" in league_name:
        return [
            {"home": "Bayern Munich", "away": "Dortmund", "time": f"{tomorrow} 19:30"},
            {"home": "RB Leipzig", "away": "Bayer Leverkusen", "time": f"{today} 18:00"}
        ]
    elif "Liga 1" in league_name:
        return [
            {"home": "PSG", "away": "Marseille", "time": f"{tomorrow} 21:45"},
            {"home": "Lyon", "away": "Monaco", "time": f"{today} 19:00"}
        ]
    else:
        return [
            {"home": "Manchester City", "away": "Liverpool", "time": f"{today} 19:45"},
            {"home": "Real Madrid", "away": "Barcelona", "time": f"{tomorrow} 21:00"}
        ]

# ---------- 7 KUN ICHIDAGI OʻYINLARNI OLISH ----------
async def fetch_matches_by_league(league_id: int):
    today = datetime.now().strftime("%Y-%m-%d")
    seven_days = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    season = get_current_season()
    
    url = f"https://{API_HOST}/v3/fixtures"
    headers = {"x-apisports-key": API_KEY}
    params = {
        "league": league_id,
        "season": season,
        "from": today,
        "to": seven_days,
        "timezone": "Asia/Tashkent"
    }
    
    logger.info(f"Soʻrov: Liga {league_id}, mavsum {season}, dan {today} gacha {seven_days}")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                logger.info(f"HTTP javob: {resp.status}")
                
                if resp.status == 200:
                    data = await resp.json()
                    matches = data.get("response", [])
                    logger.info(f"Oʻyinlar soni: {len(matches)}")
                    
                    if len(matches) == 0:
                        return {"success": [], "use_static": True}
                    return {"success": matches, "use_static": False}
                else:
                    return {"error": f"❌ API xatolik: HTTP {resp.status}"}
    except Exception as e:
        logger.exception("Ulanish xatosi")
        return {"error": f"❌ Ulanish xatosi: {type(e).__name__}"}

# ---------- OʻYINLARNI FORMATLASH ----------
def format_matches(matches, league_name, use_static=False):
    # Agar API boʻsh boʻlsa, statik maʼlumotni ishlatamiz
    if (not matches or len(matches) == 0) and use_static:
        static = get_static_matches(league_name)
        text = f"🏆 **{league_name}** (namuna maʼlumot)\n"
        text += f"📅 {datetime.now().strftime('%d.%m.%Y')} – keyingi 7 kun\n"
        text += "━" * 35 + "\n"
        for m in static:
            text += f"• {m['home']} – {m['away']}  ⏳ {m['time']}\n"
        text += "\n⚠️ *API real vaqt maʼlumot bermadi, namuna koʻrsatilmoqda*"
        return text
    
    if not matches:
        return f"⚽ {league_name}\n7 kun ichida oʻyinlar yoʻq."
    
    text = f"🏆 **{league_name}**\n"
    text += f"📅 {datetime.now().strftime('%d.%m.%Y')} – keyingi 7 kun\n"
    text += "━" * 35 + "\n"
    
    for match in matches[:10]:
        fixture = match["fixture"]
        teams = match["teams"]
        goals = match["goals"]
        status = fixture["status"]["short"]
        match_date = fixture["date"][:10]
        match_time = fixture["date"][11:16]
        
        date_obj = datetime.strptime(match_date, "%Y-%m-%d")
        date_str = date_obj.strftime("%d.%m")
        
        if status == "LIVE":
            status_icon = "🟢"
            score = f"{goals['home']}:{goals['away']}"
        elif status == "HT":
            status_icon = "🟡"
            score = f"{goals['home']}:{goals['away']}"
        elif status == "FT":
            status_icon = "✅"
            score = f"**{goals['home']}:{goals['away']}**"
        else:
            status_icon = "⏳"
            score = f"{date_str} {match_time}"
        
        text += f"• {teams['home']['name']} – {teams['away']['name']}  {score}  {status_icon}\n"
    
    return text

# ---------- TELEGRAM HANDLERLAR ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Assalomu alaykum, {user.first_name}!\n"
        "Quyidagi chempionatlardan birini tanlang – 7 kun ichidagi oʻyinlar:",
        reply_markup=get_leagues_keyboard()
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    league_id = int(query.data.split("_")[1])
    league_info = TOP_LEAGUES[league_id]
    
    await query.edit_message_text(f"⏳ {league_info['name']} – oʻyinlar yuklanmoqda...")
    result = await fetch_matches_by_league(league_id)
    
    if "error" in result:
        text = result["error"]
    else:
        text = format_matches(
            result.get("success", []), 
            league_info['name'],
            result.get("use_static", False)
        )
    
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_leagues_keyboard()
    )

async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """API javobini toʻliq koʻrsatadi"""
    league_id = 39  # Angliya
    today = datetime.now().strftime("%Y-%m-%d")
    seven_days = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    season = get_current_season()
    
    url = f"https://{API_HOST}/v3/fixtures"
    headers = {"x-apisports-key": API_KEY}
    params = {
        "league": league_id,
        "season": season,
        "from": today,
        "to": seven_days,
        "timezone": "Asia/Tashkent"
    }
    
    msg = await update.message.reply_text("🔍 API soʻrovi yuborilmoqda...")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                text = await resp.text()
                await msg.edit_text(
                    f"**Status:** {resp.status}\n"
                    f"**URL:** {url}\n"
                    f"**Params:** {params}\n"
                    f"**Javob (boshi):**\n{text[:500]}"
                )
    except Exception as e:
        await msg.edit_text(f"❌ Xatolik: {type(e).__name__}")

async def test_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """API kaliti va ulanishni tekshiradi"""
    url = f"https://{API_HOST}/v3/status"
    headers = {"x-apisports-key": API_KEY}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    await update.message.reply_text(
                        f"✅ **API ulanishi muvaffaqiyatli!**\n"
                        f"• Status: 200 OK\n"
                        f"• Mavsum: {get_current_season()}"
                    )
                else:
                    await update.message.reply_text(f"❌ API xatolik: {resp.status}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ulanish xatosi: {type(e).__name__}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Quyidagi chempionatlardan birini tanlang:",
        reply_markup=get_leagues_keyboard()
    )

# ---------- WEB SERVER (Railway uchun) ----------
async def health_check(request):
    return web.Response(text="✅ Bot ishlamoqda")

async def run_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Web server port {port} da ishga tushdi")

# ---------- ASOSIY ----------
async def run_bot():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN topilmadi!")
        return
    
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("test", test_api))
    application.add_handler(CommandHandler("debug", debug))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    logger.info("🤖 Bot ishga tushdi! (API kalit kodga yozilgan, 7 kunlik oʻyinlar)")
    
    while True:
        await asyncio.sleep(3600)

async def main():
    await asyncio.gather(
        run_web_server(),
        run_bot()
    )

if __name__ == "__main__":
    asyncio.run(main())
