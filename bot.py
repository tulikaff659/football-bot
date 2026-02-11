import os
import asyncio
import logging
import aiohttp
from datetime import datetime, timedelta
from aiohttp import web
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ---------- SOZLAMALAR ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# RapidAPI kaliti va host – MUHIT OʻZGARUVCHISIDAN OLINADI
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"

# Top 5 chempionat IDlari (API-FOOTBALL boʻyicha)
TOP_LEAGUES = {
    39: "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premyer Liga (Angliya)",
    140: "🇪🇸 La Liga (Ispaniya)",
    135: "🇮🇹 Seriya A (Italiya)",
    78: "🇩🇪 Bundesliga (Germaniya)",
    61: "🇫🇷 Liga 1 (Fransiya)"
}

# ---------- 24 SOAT ICHIDAGI OʻYINLARNI OLISH ----------
async def fetch_todays_matches():
    """API-FOOTBALL orqali bugun va ertangi oʻyinlarni olish"""
    
    if not RAPIDAPI_KEY:
        logger.warning("RAPIDAPI_KEY topilmadi, statik maʼlumot ishlatiladi")
        return get_static_matches()

    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST
    }
    
    all_matches = []
    
    try:
        async with aiohttp.ClientSession() as session:
            for league_id, league_name in TOP_LEAGUES.items():
                url = "https://api-football-v1.p.rapidapi.com/v3/fixtures"
                params = {
                    "league": league_id,
                    "season": "2024",  # Joriy mavsum
                    "from": today,
                    "to": tomorrow,
                    "timezone": "Asia/Tashkent"
                }
                
                async with session.get(url, headers=headers, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("response"):
                            for match in data["response"]:
                                fixture = match["fixture"]
                                teams = match["teams"]
                                goals = match["goals"]
                                status = match["fixture"]["status"]["short"]
                                
                                # Statusni oʻzbekchalashtirish
                                status_uz = "⏳ Tayinlangan"
                                if status == "LIVE":
                                    status_uz = "🟢 Jonli"
                                elif status == "HT":
                                    status_uz = "🟡 Tanaffus"
                                elif status == "FT":
                                    status_uz = "✅ Tugagan"
                                elif status == "PEN":
                                    status_uz = "⚪ Penaltilar"
                                
                                match_info = {
                                    "league": league_name,
                                    "home": teams["home"]["name"],
                                    "away": teams["away"]["name"],
                                    "date": fixture["date"][:10],
                                    "time": fixture["date"][11:16],
                                    "status": status_uz,
                                    "score_home": goals["home"],
                                    "score_away": goals["away"],
                                    "event_id": fixture["id"]
                                }
                                all_matches.append(match_info)
                    else:
                        logger.error(f"API xatolik {league_name}: {resp.status}")
                    
                    # Rate limit uchun pauza (bepul rejada 10 req/min)
                    await asyncio.sleep(6)
                    
    except Exception as e:
        logger.error(f"Soʻrovda xatolik: {e}")
        return get_static_matches()
    
    return all_matches if all_matches else get_static_matches()

# ---------- STATIK ZAXIRA (API ISHLAMASA) ----------
def get_static_matches():
    """Agar API vaqtincha ishlamasa, namuna maʼlumot"""
    today = datetime.now().strftime("%d.%m.%Y")
    return [
        {
            "league": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premyer Liga (Angliya)",
            "home": "Manchester City",
            "away": "Liverpool",
            "date": today,
            "time": "19:45",
            "status": "⏳ Tayinlangan",
            "score_home": None,
            "score_away": None,
            "event_id": 1145515
        },
        {
            "league": "🇪🇸 La Liga (Ispaniya)",
            "home": "Real Madrid",
            "away": "Barcelona",
            "date": today,
            "time": "21:00",
            "status": "⏳ Tayinlangan",
            "score_home": None,
            "score_away": None,
            "event_id": 1145516
        }
    ]

# ---------- XABAR FORMATLASH ----------
def format_matches_message(matches):
    """Oʻyinlar roʻyxatini chiroyli matnga aylantirish"""
    if not matches:
        return "⚽ Bugun va ertaga top 5 chempionatlarda oʻyinlar yoʻq."
    
    message = f"📅 **{datetime.now().strftime('%d.%m.%Y')} – 24 soatlik oʻyinlar**\n\n"
    message += "⏰ Toshkent vaqti boʻyicha\n\n"
    
    current_league = None
    for match in matches:
        if match["league"] != current_league:
            current_league = match["league"]
            message += f"\n🏆 **{current_league}**\n"
            message += "━" * 35 + "\n"
        
        if match["score_home"] is not None and match["score_away"] is not None:
            score = f" **{match['score_home']}:{match['score_away']}**"
        else:
            score = f" {match['time']}"
        
        status_icon = match["status"]
        message += f"• {match['home']} – {match['away']}{score}  {status_icon}\n"
    
    message += "\n📊 *Maʼlumotlar API-FOOTBALL (RapidAPI) orqali olinmoqda*"
    return message

# ---------- TELEGRAM HANDLERLAR ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start komandasi"""
    user = update.effective_user
    await update.message.reply_text(
        f"👋 Assalomu alaykum, {user.first_name}!\n"
        "⚽ Bugungi top 5 chempionat oʻyinlarini yuklayapman..."
    )
    
    matches = await fetch_todays_matches()
    message = format_matches_message(matches)
    await update.message.reply_text(message, parse_mode="Markdown")

async def matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Oʻyinlar komandasi"""
    msg = await update.message.reply_text("⏳ Maʼlumotlarni yuklayapman...")
    matches = await fetch_todays_matches()
    message = format_matches_message(matches)
    await msg.edit_text(message, parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Har qanday xabarga javob"""
    await matches(update, context)

# ---------- WEB SERVER (RAILWAY UCHUN) ----------
async def health_check(request):
    return web.Response(text="✅ Bot ishlamoqda (API-FOOTBALL)")

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
        logger.error("BOT_TOKEN topilmadi! Bot ishga tushmaydi.")
        return
    
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("matches", matches))
    application.add_handler(CommandHandler("oyinlar", matches))
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
