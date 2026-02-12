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
FOOTBALL_DATA_KEY = os.environ.get("FOOTBALL_DATA_KEY")
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

# Necha kun ichidagi o'yinlar
DAYS_AHEAD = 7  # 7 kun ichidagi o'yinlar

# ---------- ASOSIY LIGA TUGMALARI ----------
def get_leagues_keyboard():
    """Ligalar ro'yxatini qaytaradi"""
    keyboard = []
    for league_code, data in TOP_LEAGUES.items():
        keyboard.append([InlineKeyboardButton(data["name"], callback_data=f"league_{league_code}")])
    return InlineKeyboardMarkup(keyboard)

# ---------- O'YINLARNI API ORQALI OLISH ----------
async def fetch_matches_by_league(league_code: str):
    """Berilgan liga kodi bo'yicha o'yinlarni olish"""
    if not FOOTBALL_DATA_KEY:
        return {"error": "❌ FOOTBALL_DATA_KEY muhit oʻzgaruvchisida topilmadi!"}
    
    today = datetime.now().strftime("%Y-%m-%d")
    end_date = (datetime.now() + timedelta(days=DAYS_AHEAD)).strftime("%Y-%m-%d")
    
    headers = {"X-Auth-Token": FOOTBALL_DATA_KEY}
    params = {
        "competitions": league_code,
        "dateFrom": today,
        "dateTo": end_date,
        "status": "SCHEDULED,LIVE,IN_PLAY,PAUSED,FINISHED"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(FOOTBALL_DATA_URL, headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    matches = data.get("matches", [])
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

# ---------- O'YINLAR UCHUN TUGMALAR YARATISH ----------
def build_matches_keyboard(matches, league_code):
    """O'yinlar ro'yxatidan inline tugmalar yaratish"""
    keyboard = []
    
    for match in matches[:10]:  # Eng ko'pi 10 ta o'yin
        home = match["homeTeam"]["name"]
        away = match["awayTeam"]["name"]
        match_date = datetime.strptime(match["utcDate"], "%Y-%m-%dT%H:%M:%SZ")
        tashkent_time = match_date + timedelta(hours=5)
        date_str = tashkent_time.strftime("%d.%m %H:%M")
        
        # O'yin nomi: "Manchester City – Liverpool (14.02 19:45)"
        button_text = f"{home} – {away} ({date_str})"
        callback_data = f"match_{match['id']}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    # Orqaga qaytish tugmasi
    keyboard.append([InlineKeyboardButton("🔙 Back to Leagues", callback_data="leagues")])
    
    return InlineKeyboardMarkup(keyboard)

# ---------- TELEGRAM HANDLERLAR ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start komandasi – ligalarni ko'rsatadi"""
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Assalomu alaykum, {user.first_name}!\n"
        f"Quyidagi chempionatlardan birini tanlang – {DAYS_AHEAD} kun ichidagi oʻyinlar:",
        reply_markup=get_leagues_keyboard()
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Barcha inline tugmalar uchun asosiy handler"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    # -------------------- LIGALAR RO'YXATIGA QAYTISH --------------------
    if data == "leagues":
        await query.edit_message_text(
            "Quyidagi chempionatlardan birini tanlang:",
            reply_markup=get_leagues_keyboard()
        )
        return
    
    # -------------------- LIGA TANLASH --------------------
    if data.startswith("league_"):
        league_code = data.split("_")[1]
        league_info = TOP_LEAGUES.get(league_code)
        
        if not league_info:
            await query.edit_message_text("❌ Notoʻgʻri tanlov.")
            return
        
        # Yuklanayotgan xabar
        await query.edit_message_text(f"⏳ {league_info['name']} – oʻyinlar yuklanmoqda...")
        
        # API dan ma'lumot olish
        result = await fetch_matches_by_league(league_code)
        
        if "error" in result:
            await query.edit_message_text(
                result["error"],
                reply_markup=get_leagues_keyboard()
            )
            return
        
        matches = result["success"]
        
        if not matches:
            await query.edit_message_text(
                f"⚽ {league_info['name']}\n{DAYS_AHEAD} kun ichida oʻyinlar yoʻq.",
                reply_markup=get_leagues_keyboard()
            )
            return
        
        # O'yinlar ro'yxatini tugmalar shaklida ko'rsatish
        keyboard = build_matches_keyboard(matches, league_code)
        await query.edit_message_text(
            f"🏆 **{league_info['name']}** – {DAYS_AHEAD} kun ichidagi oʻyinlar:\n\n"
            "Quyidagi oʻyinlardan birini tanlang:",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    
    # -------------------- O'YIN TANLASH --------------------
    elif data.startswith("match_"):
        match_id = data.split("_")[1]
        
        # Hozircha oddiy xabar – keyinroq tahlil qo'shiladi
        await query.edit_message_text(
            f"⚽ **O'yin tahlili**\n\n"
            f"🆔 Match ID: `{match_id}`\n"
            f"📊 Ma'lumot yig'ilmoqda...\n\n"
            f"⏳ Ekspertlar tahlili, kutilayotgan tarkib va bashoratlar tez orada qoʻshiladi.",
            parse_mode="Markdown"
        )
        
        # ORQAGA QAYTISH UCHUN – bu xabardan keyin foydalanuvchi orqaga qaytishi kerak
        # Qulaylik uchun "🔙 Back to Leagues" tugmasini qo'shamiz
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Leagues", callback_data="leagues")]
        ])
        await query.message.reply_text(
            "Boshqa ligaga oʻtish:",
            reply_markup=keyboard
        )

# ---------- YORDAMCHI BUYRUQLAR (TEST, DEBUG) ----------
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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Har qanday matnli xabarga javob"""
    await update.message.reply_text(
        "Quyidagi chempionatlardan birini tanlang:",
        reply_markup=get_leagues_keyboard()
    )

# ---------- WEB SERVER (Railway uchun) ----------
async def health_check(request):
    return web.Response(text="✅ Football-Data.org bot ishlamoqda (2 bosqichli tanlov)")

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
    logger.info("🤖 Bot ishga tushdi! (Football-Data.org, 7 kunlik oʻyinlar, tahlil uchun tayyor)")
    
    while True:
        await asyncio.sleep(3600)

async def main():
    await asyncio.gather(
        run_web_server(),
        run_bot()
    )

if __name__ == "__main__":
    asyncio.run(main())
