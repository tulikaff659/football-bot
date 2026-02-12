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

# MUHIT OʻZGARUVCHISIDAN OLINADI (Railway Variables)
API_KEY = os.environ.get("API_FOOTBALL_KEY")   # Sizning kalitingiz shu yerga yoziladi
API_HOST = "v3.football.api-sports.io"         # Dashboard API host

# Top 5 chempionat (ID, nom)
TOP_LEAGUES = {
    39: {"name": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premyer Liga", "country": "Angliya"},
    140: {"name": "🇪🇸 La Liga", "country": "Ispanija"},
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

# ---------- API ORQALI OʻYINLARNI OLISH ----------
async def fetch_matches_by_league(league_id: int):
    """API-FOOTBALL Dashboard orqali 24 soatlik o'yinlar"""
    if not API_KEY:
        return {"error": "❌ API_FOOTBALL_KEY muhit oʻzgaruvchisida topilmadi!"}
    
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    season = get_current_season()
    
    url = f"https://{API_HOST}/v3/fixtures"
    headers = {
        "x-apisports-key": API_KEY
    }
    params = {
        "league": league_id,
        "season": season,
        "from": today,
        "to": tomorrow,
        "timezone": "Asia/Tashkent"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    matches = data.get("response", [])
                    return {"success": matches}
                elif resp.status == 401:
                    return {"error": "❌ API kaliti notoʻgʻri. Dashboarddan yangi kalit oling."}
                elif resp.status == 429:
                    return {"error": "❌ Kunlik soʻrovlar limiti oshib ketdi. Ertaga qayta urinib koʻring."}
                else:
                    return {"error": f"❌ API xatolik: HTTP {resp.status}"}
    except Exception as e:
        return {"error": f"❌ Ulanish xatosi: {type(e).__name__}"}

# ---------- OʻYINLARNI FORMATLASH ----------
def format_matches(matches, league_name):
    if not matches:
        return f"⚽ {league_name}\n24 soat ichida oʻyinlar yoʻq."
    
    text = f"🏆 **{league_name}**\n"
    text += f"📅 {datetime.now().strftime('%d.%m.%Y')} – ertaga\n"
    text += "━" * 35 + "\n"
    
    for match in matches[:10]:
        fixture = match["fixture"]
        teams = match["teams"]
        goals = match["goals"]
        status = fixture["status"]["short"]
        match_time = fixture["date"][11:16]
        
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
            score = match_time
        
        text += f"• {teams['home']['name']} – {teams['away']['name']}  {score}  {status_icon}\n"
    
    return text

# ---------- TELEGRAM HANDLERLAR ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Assalomu alaykum, {user.first_name}!\n"
        "Quyidagi chempionatlardan birini tanlang:",
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
        text = format_matches(result["success"], league_info['name'])
    
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_leagues_keyboard()
    )

async def test_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """API statusini tekshirish"""
    if not API_KEY:
        await update.message.reply_text("❌ API_FOOTBALL_KEY muhit oʻzgaruvchisida topilmadi!")
        return
    
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
    return web.Response(text="✅ Bot ishlamoqda (API-FOOTBALL Dashboard)")

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
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    logger.info("🤖 Bot ishga tushdi! API-FOOTBALL Dashboard ulandi")
    
    while True:
        await asyncio.sleep(3600)

async def main():
    await asyncio.gather(
        run_web_server(),
        run_bot()
    )

if __name__ == "__main__":
    asyncio.run(main())
