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

# ========== FOOTBALL-DATA.ORG SOZLAMALARI ==========
FOOTBALL_DATA_KEY = os.environ.get("FOOTBALL_DATA_KEY")  # Emailda kelgan kalit
FOOTBALL_DATA_HOST = "api.football-data.org"
FOOTBALL_DATA_URL = "https://api.football-data.org/v4/matches"

# Top 5 chempionat kodlari (Football-Data.org bo'yicha)
TOP_LEAGUES = {
    "PL": {"name": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premyer Liga", "country": "Angliya"},
    "PD": {"name": "🇪🇸 La Liga", "country": "Ispaniya"},
    "SA": {"name": "🇮🇹 Seriya A", "country": "Italiya"},
    "BL1": {"name": "🇩🇪 Bundesliga", "country": "Germaniya"},
    "FL1": {"name": "🇫🇷 Liga 1", "country": "Fransiya"}
}

# Necha kun ichidagi o'yinlar (4 kun qilib belgilangan)
DAYS_AHEAD = 4

# ---------- INLINE TUGMALAR ----------
def get_leagues_keyboard():
    keyboard = []
    for league_code, data in TOP_LEAGUES.items():
        keyboard.append([InlineKeyboardButton(data["name"], callback_data=f"league_{league_code}")])
    return InlineKeyboardMarkup(keyboard)

# ---------- FOOTBALL-DATA.ORG ORQALI OʻYINLARNI OLISH ----------
async def fetch_matches_by_league(league_code: str):
    """Berilgan liga kodi bo'yicha 4 kun ichidagi o'yinlarni olish"""
    if not FOOTBALL_DATA_KEY:
        return {"error": "❌ FOOTBALL_DATA_KEY muhit oʻzgaruvchisida topilmadi!"}
    
    today = datetime.now().strftime("%Y-%m-%d")
    end_date = (datetime.now() + timedelta(days=DAYS_AHEAD)).strftime("%Y-%m-%d")
    
    headers = {"X-Auth-Token": FOOTBALL_DATA_KEY}
    params = {
        "competitions": league_code,
        "dateFrom": today,
        "dateTo": end_date,
        "status": "SCHEDULED,LIVE,IN_PLAY,PAUSED,FINISHED"  # Barcha holatlar
    }
    
    logger.info(f"Soʻrov: Liga {league_code}, dan {today} gacha {end_date}")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                FOOTBALL_DATA_URL, 
                headers=headers, 
                params=params
            ) as resp:
                logger.info(f"HTTP javob: {resp.status}")
                
                if resp.status == 200:
                    data = await resp.json()
                    matches = data.get("matches", [])
                    logger.info(f"Oʻyinlar soni: {len(matches)}")
                    return {"success": matches}
                elif resp.status == 401:
                    return {"error": "❌ API kaliti notoʻgʻri. Football-Data.org dan yangi kalit oling."}
                elif resp.status == 429:
                    return {"error": "❌ Soʻrovlar limiti oshib ketdi. Bir daqiqa kuting."}
                else:
                    return {"error": f"❌ API xatolik: HTTP {resp.status}"}
    except Exception as e:
        logger.exception("Ulanish xatosi")
        return {"error": f"❌ Ulanish xatosi: {type(e).__name__}"}

# ---------- OʻYINLARNI FORMATLASH ----------
def format_matches(matches, league_name):
    if not matches:
        return f"⚽ {league_name}\n{DAYS_AHEAD} kun ichida oʻyinlar yoʻq."
    
    text = f"🏆 **{league_name}**\n"
    text += f"📅 {datetime.now().strftime('%d.%m.%Y')} – keyingi {DAYS_AHEAD} kun\n"
    text += "━" * 35 + "\n"
    
    for match in matches:
        home = match["homeTeam"]["name"]
        away = match["awayTeam"]["name"]
        match_date = datetime.strptime(match["utcDate"], "%Y-%m-%dT%H:%M:%SZ")
        tashkent_time = match_date + timedelta(hours=5)  # UTC+5 (Toshkent)
        date_str = tashkent_time.strftime("%d.%m %H:%M")
        status = match["status"]
        
        # Statusga qarab ikonka va hisob
        if status == "FINISHED":
            status_icon = "✅"
            score_home = match["score"]["fullTime"]["home"]
            score_away = match["score"]["fullTime"]["away"]
            if score_home is None or score_away is None:
                score = f"⏳ {date_str}"
            else:
                score = f"**{score_home}:{score_away}**"
        elif status in ["LIVE", "IN_PLAY", "PAUSED"]:
            status_icon = "🟢"
            score_home = match["score"]["fullTime"]["home"] or match["score"]["halfTime"]["home"] or 0
            score_away = match["score"]["fullTime"]["away"] or match["score"]["halfTime"]["away"] or 0
            score = f"{score_home}:{score_away}"
        else:  # SCHEDULED, TIMED
            status_icon = "⏳"
            score = date_str
        
        text += f"• {home} – {away}  {score}  {status_icon}\n"
    
    return text

# ---------- TELEGRAM HANDLERLAR ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Assalomu alaykum, {user.first_name}!\n"
        f"Quyidagi chempionatlardan birini tanlang – {DAYS_AHEAD} kun ichidagi oʻyinlar:",
        reply_markup=get_leagues_keyboard()
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    league_code = query.data.split("_")[1]  # "league_PL" -> "PL"
    league_info = TOP_LEAGUES[league_code]
    
    await query.edit_message_text(f"⏳ {league_info['name']} – oʻyinlar yuklanmoqda...")
    result = await fetch_matches_by_league(league_code)
    
    if "error" in result:
        text = result["error"]
    else:
        text = format_matches(result["success"], league_info['name'])
    
    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_leagues_keyboard()
    )

async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """API javobini toʻliq koʻrsatadi (Premyer Liga misolida)"""
    if not FOOTBALL_DATA_KEY:
        await update.message.reply_text("❌ FOOTBALL_DATA_KEY topilmadi!")
        return
    
    league_code = "PL"
    today = datetime.now().strftime("%Y-%m-%d")
    end_date = (datetime.now() + timedelta(days=DAYS_AHEAD)).strftime("%Y-%m-%d")
    
    url = FOOTBALL_DATA_URL
    headers = {"X-Auth-Token": FOOTBALL_DATA_KEY}
    params = {
        "competitions": league_code,
        "dateFrom": today,
        "dateTo": end_date
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
    if not FOOTBALL_DATA_KEY:
        await update.message.reply_text("❌ FOOTBALL_DATA_KEY topilmadi!")
        return
    
    url = "https://api.football-data.org/v4/competitions/PL/matches?dateFrom=2026-02-01&dateTo=2026-02-28"
    headers = {"X-Auth-Token": FOOTBALL_DATA_KEY}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    await update.message.reply_text("✅ **API ulanishi muvaffaqiyatli!**")
                else:
                    await update.message.reply_text(f"❌ API xatolik: {resp.status}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ulanish xatosi: {type(e).__name__}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Quyidagi chempionatlardan birini tanlang – {DAYS_AHEAD} kun ichidagi oʻyinlar:",
        reply_markup=get_leagues_keyboard()
    )

# ---------- WEB SERVER (Railway uchun) ----------
async def health_check(request):
    return web.Response(text="✅ Football-Data.org bot ishlamoqda")

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
    logger.info("🤖 Bot ishga tushdi! (Football-Data.org, 4 kunlik oʻyinlar)")
    
    while True:
        await asyncio.sleep(3600)

async def main():
    await asyncio.gather(
        run_web_server(),
        run_bot()
    )

if __name__ == "__main__":
    asyncio.run(main())
