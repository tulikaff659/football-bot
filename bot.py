import os
import asyncio
import logging
import aiohttp
import aiosqlite
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
DAYS_AHEAD = 7

# ========== MAʼLUMOTLAR BAZASI (SQLite) ==========
DB_PATH = "data/bot.db"  # Railway volume uchun

async def init_db():
    """Bazani ishga tushirish – jadvallarni yaratish"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        # Adminlar jadvali
        await db.execute('''
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_by INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Tahlillar jadvali
        await db.execute('''
            CREATE TABLE IF NOT EXISTS match_analyses (
                match_id INTEGER PRIMARY KEY,
                analysis TEXT NOT NULL,
                added_by INTEGER NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.commit()
        
        # Asosiy adminni qo'shish (agar mavjud bo'lmasa)
        MAIN_ADMIN = 6935090105
        async with db.execute('SELECT user_id FROM admins WHERE user_id = ?', (MAIN_ADMIN,)) as cursor:
            admin = await cursor.fetchone()
            if not admin:
                await db.execute('INSERT INTO admins (user_id, added_by) VALUES (?, ?)', (MAIN_ADMIN, MAIN_ADMIN))
                await db.commit()
                logger.info(f"Asosiy admin qo'shildi: {MAIN_ADMIN}")

async def is_admin(user_id: int) -> bool:
    """Foydalanuvchi admin ekanligini tekshirish"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT 1 FROM admins WHERE user_id = ?', (user_id,)) as cursor:
            return await cursor.fetchone() is not None

async def add_admin(user_id: int, added_by: int) -> bool:
    """Yangi admin qo'shish"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('INSERT INTO admins (user_id, added_by) VALUES (?, ?)', (user_id, added_by))
            await db.commit()
            return True
    except:
        return False

async def remove_admin(user_id: int) -> bool:
    """Adminni olib tashlash"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM admins WHERE user_id = ?', (user_id,))
        await db.commit()
        return True

async def get_all_admins():
    """Barcha adminlar ro'yxatini olish"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT user_id, added_by, added_at FROM admins ORDER BY added_at') as cursor:
            return await cursor.fetchall()

async def add_analysis(match_id: int, analysis: str, added_by: int):
    """O'yin tahlilini saqlash (mavjud bo'lsa yangilash)"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT INTO match_analyses (match_id, analysis, added_by)
            VALUES (?, ?, ?)
            ON CONFLICT(match_id) DO UPDATE SET
                analysis = excluded.analysis,
                added_by = excluded.added_by,
                added_at = CURRENT_TIMESTAMP
        ''', (match_id, analysis, added_by))
        await db.commit()

async def get_analysis(match_id: int):
    """O'yin tahlilini olish"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT analysis, added_at FROM match_analyses WHERE match_id = ?', (match_id,)) as cursor:
            return await cursor.fetchone()

# ---------- INLINE TUGMALAR ----------
def get_leagues_keyboard():
    """Ligalar ro'yxatini qaytaradi"""
    keyboard = []
    for league_code, data in TOP_LEAGUES.items():
        keyboard.append([InlineKeyboardButton(data["name"], callback_data=f"league_{league_code}")])
    return InlineKeyboardMarkup(keyboard)

def build_matches_keyboard(matches, league_code):
    """O'yinlar ro'yxatidan inline tugmalar yaratish"""
    keyboard = []
    for match in matches[:10]:
        home = match["homeTeam"]["name"]
        away = match["awayTeam"]["name"]
        match_date = datetime.strptime(match["utcDate"], "%Y-%m-%dT%H:%M:%SZ")
        tashkent_time = match_date + timedelta(hours=5)
        date_str = tashkent_time.strftime("%d.%m %H:%M")
        
        button_text = f"{home} – {away} ({date_str})"
        callback_data = f"match_{match['id']}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    keyboard.append([InlineKeyboardButton("🔙 Back to Leagues", callback_data="leagues")])
    return InlineKeyboardMarkup(keyboard)

# ---------- API ORQALI OʻYINLARNI OLISH ----------
async def fetch_matches_by_league(league_code: str):
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
        
        await query.edit_message_text(f"⏳ {league_info['name']} – oʻyinlar yuklanmoqda...")
        result = await fetch_matches_by_league(league_code)
        
        if "error" in result:
            await query.edit_message_text(result["error"], reply_markup=get_leagues_keyboard())
            return
        
        matches = result["success"]
        if not matches:
            await query.edit_message_text(
                f"⚽ {league_info['name']}\n{DAYS_AHEAD} kun ichida oʻyinlar yoʻq.",
                reply_markup=get_leagues_keyboard()
            )
            return
        
        keyboard = build_matches_keyboard(matches, league_code)
        await query.edit_message_text(
            f"🏆 **{league_info['name']}** – {DAYS_AHEAD} kun ichidagi oʻyinlar:\n\n"
            "Quyidagi oʻyinlardan birini tanlang:",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
    
    # -------------------- O'YIN TANLASH --------------------
    elif data.startswith("match_"):
        match_id = int(data.split("_")[1])
        
        # Tahlilni bazadan olish
        analysis_row = await get_analysis(match_id)
        
        if analysis_row:
            analysis_text, added_at = analysis_row
            added_at_dt = datetime.strptime(added_at, "%Y-%m-%d %H:%M:%S")
            date_str = added_at_dt.strftime("%d.%m.%Y %H:%M")
            
            message = (
                f"⚽ **Oʻyin tahlili**\n\n"
                f"🆔 Match ID: `{match_id}`\n"
                f"📝 **Tahlil:**\n{analysis_text}\n\n"
                f"🕐 Qoʻshilgan sana: {date_str}"
            )
        else:
            message = (
                f"⚽ **Oʻyin tahlili**\n\n"
                f"🆔 Match ID: `{match_id}`\n"
                f"📊 Hozircha bu oʻyin uchun tahlil mavjud emas.\n\n"
            )
            # Agar admin bo'lsa, tahlil qo'shish buyrug'ini eslatish
            if await is_admin(update.effective_user.id):
                message += f"💡 Admin: `/addanalysis {match_id} <tahlil matni>` buyrugʻi bilan tahlil qoʻshishingiz mumkin."
        
        await query.edit_message_text(message, parse_mode="Markdown")
        
        # Orqaga qaytish tugmasi
        back_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Leagues", callback_data="leagues")]
        ])
        await query.message.reply_text("Boshqa ligaga oʻtish:", reply_markup=back_keyboard)

# ---------- ADMIN BUYRUQLARI ----------
async def add_analysis_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /addanalysis <match_id> <tahlil matni> """
    user = update.effective_user
    if not await is_admin(user.id):
        await update.message.reply_text("❌ Siz admin emassiz. Bu buyruq faqat adminlar uchun.")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ Notoʻgʻri format. Ishlatish:\n"
            "`/addanalysis 123456 Manchester City hujumda kuchli...`",
            parse_mode="Markdown"
        )
        return
    
    try:
        match_id = int(context.args[0])
        analysis_text = ' '.join(context.args[1:])
    except ValueError:
        await update.message.reply_text("❌ Match ID raqam boʻlishi kerak.")
        return
    
    await add_analysis(match_id, analysis_text, user.id)
    await update.message.reply_text(f"✅ Tahlil muvaffaqiyatli qoʻshildi (Match ID: {match_id}).")

async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /addadmin <user_id> """
    user = update.effective_user
    if not await is_admin(user.id):
        await update.message.reply_text("❌ Siz admin emassiz. Bu buyruq faqat adminlar uchun.")
        return
    
    if len(context.args) != 1:
        await update.message.reply_text("❌ Ishlatish: `/addadmin 123456789`", parse_mode="Markdown")
        return
    
    try:
        new_admin_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ User ID raqam boʻlishi kerak.")
        return
    
    if await is_admin(new_admin_id):
        await update.message.reply_text("⚠️ Bu foydalanuvchi allaqachon admin.")
        return
    
    success = await add_admin(new_admin_id, user.id)
    if success:
        await update.message.reply_text(f"✅ Foydalanuvchi {new_admin_id} admin qilindi.")
    else:
        await update.message.reply_text("❌ Xatolik yuz berdi.")

async def remove_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /removeadmin <user_id> """
    user = update.effective_user
    if not await is_admin(user.id):
        await update.message.reply_text("❌ Siz admin emassiz.")
        return
    
    if len(context.args) != 1:
        await update.message.reply_text("❌ Ishlatish: `/removeadmin 123456789`", parse_mode="Markdown")
        return
    
    try:
        admin_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ User ID raqam boʻlishi kerak.")
        return
    
    # Asosiy adminni o'chirib bo'lmaydi
    if admin_id == 6935090105:
        await update.message.reply_text("❌ Asosiy adminni o'chirib bo'lmaydi.")
        return
    
    if not await is_admin(admin_id):
        await update.message.reply_text("⚠️ Bu foydalanuvchi admin emas.")
        return
    
    await remove_admin(admin_id)
    await update.message.reply_text(f"✅ Foydalanuvchi {admin_id} adminlikdan olib tashlandi.")

async def list_admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ /listadmins """
    user = update.effective_user
    if not await is_admin(user.id):
        await update.message.reply_text("❌ Siz admin emassiz.")
        return
    
    admins = await get_all_admins()
    if not admins:
        await update.message.reply_text("📭 Adminlar ro'yxati bo'sh.")
        return
    
    text = "👑 **Adminlar ro'yxati:**\n\n"
    for admin_id, added_by, added_at in admins:
        added_at_dt = datetime.strptime(added_at, "%Y-%m-%d %H:%M:%S")
        date_str = added_at_dt.strftime("%d.%m.%Y")
        text += f"• `{admin_id}` – qo'shdi: `{added_by}`, sana: {date_str}\n"
    
    await update.message.reply_text(text, parse_mode="Markdown")

# ---------- TEST / DEBUG BUYRUQLARI ----------
async def test_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    await update.message.reply_text(
        "Quyidagi chempionatlardan birini tanlang:",
        reply_markup=get_leagues_keyboard()
    )

# ---------- WEB SERVER (Railway uchun) ----------
async def health_check(request):
    return web.Response(text="✅ Bot ishlamoqda (Admin panel qo'shilgan)")

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
    
    # Bazani ishga tushirish
    await init_db()
    
    application = Application.builder().token(token).build()
    
    # Asosiy handlerlar
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("test", test_api))
    application.add_handler(CommandHandler("debug", debug))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Admin handlerlar
    application.add_handler(CommandHandler("addanalysis", add_analysis_command))
    application.add_handler(CommandHandler("addadmin", add_admin_command))
    application.add_handler(CommandHandler("removeadmin", remove_admin_command))
    application.add_handler(CommandHandler("listadmins", list_admins_command))
    
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    logger.info("🤖 Bot ishga tushdi! (Admin panel faol)")
    
    while True:
        await asyncio.sleep(3600)

async def main():
    await asyncio.gather(
        run_web_server(),
        run_bot()
    )

if __name__ == "__main__":
    asyncio.run(main())
