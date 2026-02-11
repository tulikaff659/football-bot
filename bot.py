import os
import asyncio
import logging
import aiohttp
from datetime import datetime, timedelta
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# ---------- SOZLAMALAR ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# RapidAPI kaliti va host (MUHIT OʻZGARUVCHISIDAN)
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"

# Top 5 chempionat (ID, nom, bayroq)
TOP_LEAGUES = {
    39: {"name": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premyer Liga", "country": "Angliya"},
    140: {"name": "🇪🇸 La Liga", "country": "Ispaniya"},
    135: {"name": "🇮🇹 Seriya A", "country": "Italiya"},
    78: {"name": "🇩🇪 Bundesliga", "country": "Germaniya"},
    61: {"name": "🇫🇷 Liga 1", "country": "Fransiya"}
}

# ---------- INLINE TUGMALAR ----------
def get_leagues_keyboard():
    """5 ta chempionat uchun inline tugmalar yaratish"""
    keyboard = []
    for league_id, data in TOP_LEAGUES.items():
        keyboard.append([InlineKeyboardButton(
            text=data["name"],
            callback_data=f"league_{league_id}"
        )])
    return InlineKeyboardMarkup(keyboard)

# ---------- API ORQALI OʻYINLARNI OLISH ----------
async def fetch_matches_by_league(league_id: int):
    """Berilgan liga ID boʻyicha 24 soat ichidagi oʻyinlarni olish"""
    if not RAPIDAPI_KEY:
        logger.error("RAPIDAPI_KEY topilmadi!")
        return None
    
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    
    url = "https://api-football-v1.p.rapidapi.com/v3/fixtures"
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST
    }
    params = {
        "league": league_id,
        "season": "2024",  # 2024/2025 mavsum
        "from": today,
        "to": tomorrow,
        "timezone": "Asia/Tashkent"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("response", [])
                else:
                    logger.error(f"API xatolik: {resp.status} - Liga {league_id}")
                    return None
    except Exception as e:
        logger.error(f"Soʻrovda xatolik: {e}")
        return None

# ---------- OʻYINLARNI FORMATLASH ----------
def format_matches(matches, league_name):
    """Oʻyinlar roʻyxatini chiroyli matnga aylantirish"""
    if not matches:
        return f"⚽ {league_name} – 24 soat ichida oʻyinlar yoʻq."
    
    text = f"🏆 **{league_name}**\n"
    text += f"📅 {datetime.now().strftime('%d.%m.%Y')} – ertaga\n"
    text += "━" * 35 + "\n"
    
    for match in matches:
        fixture = match["fixture"]
        teams = match["teams"]
        goals = match["goals"]
        status = fixture["status"]["short"]
        
        # Vaqt (Toshkent vaqti)
        match_time = fixture["date"][11:16]
        
        # Statusga qarab belgi
        if status == "LIVE":
            status_icon = "🟢 Jonli"
            score = f"{goals['home']}:{goals['away']}"
        elif status == "HT":
            status_icon = "🟡 Tanaffus"
            score = f"{goals['home']}:{goals['away']}"
        elif status == "FT":
            status_icon = "✅ Tugagan"
            score = f"**{goals['home']}:{goals['away']}**"
        elif status == "PEN":
            status_icon = "⚪ Penaltilar"
            score = f"{goals['home']}:{goals['away']}"
        else:
            status_icon = "⏳"
            score = match_time
        
        home = teams["home"]["name"]
        away = teams["away"]["name"]
        
        text += f"• {home} – {away}  {score}  {status_icon}\n"
    
    text += "\n📊 *API-FOOTBALL orqali real vaqt*"
    return text

# ---------- TELEGRAM HANDLERLAR ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start komandasi – liga tanlash tugmalari"""
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Assalomu alaykum, {user.first_name}!\n"
        "Quyidagi chempionatlardan birini tanlang:\n"
        "24 soat ichidagi oʻyinlarni koʻrasiz.",
        reply_markup=get_leagues_keyboard()
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline tugma bosilganda ishlaydi"""
    query = update.callback_query
    await query.answer()
    
    # Callback ma'lumotdan liga ID sini olish
    callback_data = query.data
    if callback_data.startswith("league_"):
        league_id = int(callback_data.split("_")[1])
        league_info = TOP_LEAGUES.get(league_id)
        if not league_info:
            await query.edit_message_text("❌ Notoʻgʻri tanlov.")
            return
        
        # Yuklanayotgani haqida xabar
        await query.edit_message_text(
            f"⏳ {league_info['name']} – oʻyinlar yuklanmoqda..."
        )
        
        # API dan ma'lumot olish
        matches = await fetch_matches_by_league(league_id)
        
        if matches is None:
            text = "❌ API bilan bogʻlanishda xatolik yuz berdi.\nQayta urinib koʻring."
        else:
            text = format_matches(matches, league_info['name'])
        
        # Xabarni yangilash va tugmalarni qayta koʻrsatish
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=get_leagues_keyboard()
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Har qanday matnli xabarga tugmalar bilan javob"""
    await update.message.reply_text(
        "Quyidagi chempionatlardan birini tanlang:",
        reply_markup=get_leagues_keyboard()
    )

# ---------- WEB SERVER (Railway uchun) ----------
async def health_check(request):
    return web.Response(text="✅ Futbol bot ishlamoqda (API-FOOTBALL)")

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
    
    # Handlerlar
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    logger.info("🤖 Bot ishga tushdi! API-FOOTBALL ulandi")
    
    while True:
        await asyncio.sleep(3600)

async def main():
    await asyncio.gather(
        run_web_server(),
        run_bot()
    )

if __name__ == "__main__":
    asyncio.run(main())
