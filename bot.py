import os
import asyncio
import logging
import aiohttp
from datetime import datetime, timedelta
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler

# ---------- SOZLAMALAR ----------
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "api-football-v1.p.rapidapi.com"

TOP_LEAGUES = {
    39: "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premyer Liga (Angliya)",
    140: "🇪🇸 La Liga (Ispaniya)",
    135: "🇮🇹 Seriya A (Italiya)",
    78: "🇩🇪 Bundesliga (Germaniya)",
    61: "🇫🇷 Liga 1 (Fransiya)"
}

def get_current_season():
    now = datetime.now()
    return now.year if now.month >= 8 else now.year - 1

async def fetch_matches(league_id):
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    season = get_current_season()
    
    url = "https://api-football-v1.p.rapidapi.com/v3/fixtures"
    headers = {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": RAPIDAPI_HOST}
    params = {"league": league_id, "season": season, "from": today, "to": tomorrow, "timezone": "Asia/Tashkent"}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("response", [])
                else:
                    return None
    except:
        return None

def format_matches(matches, league_name):
    if not matches:
        return f"⚽ {league_name}\n24 soat ichida oʻyinlar yoʻq."
    text = f"🏆 **{league_name}**\n{datetime.now().strftime('%d.%m.%Y')}\n" + "━"*35 + "\n"
    for m in matches:
        home = m["teams"]["home"]["name"]
        away = m["teams"]["away"]["name"]
        time = m["fixture"]["date"][11:16]
        status = m["fixture"]["status"]["short"]
        if status == "LIVE":
            score = f"{m['goals']['home']}:{m['goals']['away']} 🟢"
        elif status == "FT":
            score = f"**{m['goals']['home']}:{m['goals']['away']}** ✅"
        else:
            score = time
        text += f"• {home} – {away}  {score}\n"
    return text

def get_keyboard():
    kb = []
    for lid, name in TOP_LEAGUES.items():
        kb.append([InlineKeyboardButton(name, callback_data=f"league_{lid}")])
    return InlineKeyboardMarkup(kb)

async def start(update, context):
    await update.message.reply_text("👋 Ligalardan birini tanlang:", reply_markup=get_keyboard())

async def button(update, context):
    q = update.callback_query
    await q.answer()
    lid = int(q.data.split("_")[1])
    league_name = TOP_LEAGUES[lid]
    await q.edit_message_text(f"⏳ {league_name} yuklanmoqda...")
    matches = await fetch_matches(lid)
    if matches is None:
        text = "❌ API bilan bogʻlanishda xatolik.\nKalit/obunani tekshiring."
    else:
        text = format_matches(matches, league_name)
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=get_keyboard())

async def test(update, context):
    if not RAPIDAPI_KEY:
        await update.message.reply_text("❌ RAPIDAPI_KEY topilmadi.")
        return
    url = "https://api-football-v1.p.rapidapi.com/v3/status"
    headers = {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": RAPIDAPI_HOST}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    await update.message.reply_text("✅ API ulanishi muvaffaqiyatli!")
                else:
                    await update.message.reply_text(f"❌ API xatolik: {resp.status}")
    except:
        await update.message.reply_text("❌ Ulanish xatosi.")

async def main():
    token = os.environ.get("BOT_TOKEN")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("test", test))
    app.add_handler(CallbackQueryHandler(button))
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    logger.info("Bot ishga tushdi")
    while True: await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
