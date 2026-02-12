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

# ========== FOOTBALL-DATA.ORG ==========
FOOTBALL_DATA_KEY = os.environ.get("FOOTBALL_DATA_KEY")
FOOTBALL_DATA_URL = "https://api.football-data.org/v4"
HEADERS = {"X-Auth-Token": FOOTBALL_DATA_KEY}

# Top 5 chempionat (kodi va nomi)
TOP_LEAGUES = {
    "PL": {"name": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premyer Liga", "country": "Angliya"},
    "PD": {"name": "🇪🇸 La Liga", "country": "Ispaniya"},
    "SA": {"name": "🇮🇹 Seriya A", "country": "Italiya"},
    "BL1": {"name": "🇩🇪 Bundesliga", "country": "Germaniya"},
    "FL1": {"name": "🇫🇷 Liga 1", "country": "Fransiya"}
}

# Necha kun ichidagi o'yinlar (7 kun)
DAYS_AHEAD = 7
DB_PATH = "data/bot.db"

# ========== MAʼLUMOTLAR BAZASI ==========
async def init_db():
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
        # Obunalar jadvali
        await db.execute('''
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER,
                match_id INTEGER,
                match_time TIMESTAMP NOT NULL,
                home_team TEXT,
                away_team TEXT,
                notified_1h BOOLEAN DEFAULT 0,
                notified_15m BOOLEAN DEFAULT 0,
                notified_lineups BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, match_id)
            )
        ''')
        await db.commit()

    # Asosiy adminni qo'shish (agar mavjud bo'lmasa)
    MAIN_ADMIN = 6935090105
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT user_id FROM admins WHERE user_id = ?', (MAIN_ADMIN,)) as cursor:
            if not await cursor.fetchone():
                await db.execute('INSERT INTO admins (user_id, added_by) VALUES (?, ?)', (MAIN_ADMIN, MAIN_ADMIN))
                await db.commit()
                logger.info(f"Asosiy admin qo'shildi: {MAIN_ADMIN}")

# ---------- Admin tekshirish ----------
async def is_admin(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT 1 FROM admins WHERE user_id = ?', (user_id,)) as cursor:
            return await cursor.fetchone() is not None

# ---------- Admin qo'shish ----------
async def add_admin(user_id: int, added_by: int) -> bool:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('INSERT INTO admins (user_id, added_by) VALUES (?, ?)', (user_id, added_by))
            await db.commit()
            return True
    except:
        return False

# ---------- Admin o'chirish ----------
async def remove_admin(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM admins WHERE user_id = ?', (user_id,))
        await db.commit()
        return True

# ---------- Adminlar ro'yxati ----------
async def get_all_admins():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT user_id, added_by, added_at FROM admins ORDER BY added_at') as cursor:
            return await cursor.fetchall()

# ---------- Tahlil qo'shish/yangilash ----------
async def add_analysis(match_id: int, analysis: str, added_by: int):
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

# ---------- Tahlil olish ----------
async def get_analysis(match_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT analysis, added_at FROM match_analyses WHERE match_id = ?', (match_id,)) as cursor:
            return await cursor.fetchone()

# ========== OBUNA TIZIMI ==========
async def subscribe_user(user_id: int, match_id: int, match_time: str, home: str, away: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT OR REPLACE INTO subscriptions 
            (user_id, match_id, match_time, home_team, away_team, notified_1h, notified_15m, notified_lineups)
            VALUES (?, ?, ?, ?, ?, 0, 0, 0)
        ''', (user_id, match_id, match_time, home, away))
        await db.commit()

async def unsubscribe_user(user_id: int, match_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM subscriptions WHERE user_id = ? AND match_id = ?', (user_id, match_id))
        await db.commit()

async def get_all_subscriptions():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT user_id, match_id, match_time, home_team, away_team, notified_1h, notified_15m, notified_lineups FROM subscriptions') as cursor:
            return await cursor.fetchall()

async def update_notification_flags(user_id: int, match_id: int, one_hour: bool = False, fifteen_min: bool = False, lineups: bool = False):
    async with aiosqlite.connect(DB_PATH) as db:
        updates = []
        params = []
        if one_hour:
            updates.append("notified_1h = 1")
        if fifteen_min:
            updates.append("notified_15m = 1")
        if lineups:
            updates.append("notified_lineups = 1")
        if not updates:
            return
        query = f"UPDATE subscriptions SET {', '.join(updates)} WHERE user_id = ? AND match_id = ?"
        params.extend([user_id, match_id])
        await db.execute(query, params)
        await db.commit()

# ========== API CALLS ==========
async def fetch_matches_by_league(league_code: str):
    if not FOOTBALL_DATA_KEY:
        return {"error": "❌ FOOTBALL_DATA_KEY topilmadi!"}
    today = datetime.now().strftime("%Y-%m-%d")
    end_date = (datetime.now() + timedelta(days=DAYS_AHEAD)).strftime("%Y-%m-%d")
    params = {
        "competitions": league_code,
        "dateFrom": today,
        "dateTo": end_date,
        "status": "SCHEDULED,LIVE,IN_PLAY,PAUSED,FINISHED"
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{FOOTBALL_DATA_URL}/matches", headers=HEADERS, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {"success": data.get("matches", [])}
                else:
                    return {"error": f"❌ API xatolik: {resp.status}"}
    except Exception as e:
        logger.exception("Ulanish xatosi")
        return {"error": f"❌ Ulanish xatosi: {type(e).__name__}"}

async def fetch_match_by_id(match_id: int):
    if not FOOTBALL_DATA_KEY:
        return {"error": "❌ FOOTBALL_DATA_KEY topilmadi!"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{FOOTBALL_DATA_URL}/matches/{match_id}", headers=HEADERS) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {"success": data}
                else:
                    return {"error": f"❌ API xatolik: {resp.status}"}
    except Exception as e:
        logger.exception("Ulanish xatosi")
        return {"error": f"❌ Ulanish xatosi: {type(e).__name__}"}

# ========== INLINE TUGMALAR ==========
def get_leagues_keyboard():
    """Ligalar ro'yxati tugmalari"""
    keyboard = []
    for code, data in TOP_LEAGUES.items():
        keyboard.append([InlineKeyboardButton(data["name"], callback_data=f"league_{code}")])
    return InlineKeyboardMarkup(keyboard)

def build_matches_keyboard(matches):
    """Oʻyinlar roʻyxati tugmalari (faqat o'yin nomlari)"""
    keyboard = []
    for match in matches[:10]:
        home = match["homeTeam"]["name"]
        away = match["awayTeam"]["name"]
        match_date = datetime.strptime(match["utcDate"], "%Y-%m-%dT%H:%M:%SZ")
        tashkent_time = match_date + timedelta(hours=5)
        date_str = tashkent_time.strftime("%d.%m %H:%M")
        match_id = match["id"]

        button_text = f"{home} – {away} ({date_str})"
        callback_data = f"match_{match_id}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    keyboard.append([InlineKeyboardButton("🔙 Back to Leagues", callback_data="leagues")])
    return InlineKeyboardMarkup(keyboard)

def build_match_detail_keyboard(match_id: int, is_subscribed: bool = False):
    """Oʻyin tahlili sahifasidagi tugmalar (Kuzatish / Bekor qilish + Back)"""
    keyboard = []
    if is_subscribed:
        keyboard.append([InlineKeyboardButton("🔕 Kuzatishni bekor qilish", callback_data=f"unsubscribe_{match_id}")])
    else:
        keyboard.append([InlineKeyboardButton("🔔 Kuzatish", callback_data=f"subscribe_{match_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Back to Leagues", callback_data="leagues")])
    return InlineKeyboardMarkup(keyboard)

# ========== TELEGRAM HANDLERLAR ==========
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
    user_id = update.effective_user.id

    # ---------- Ligalarga qaytish ----------
    if data == "leagues":
        await query.edit_message_text(
            "Quyidagi chempionatlardan birini tanlang:",
            reply_markup=get_leagues_keyboard()
        )
        return

    # ---------- Liga tanlash ----------
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
        keyboard = build_matches_keyboard(matches)
        await query.edit_message_text(
            f"🏆 **{league_info['name']}** – {DAYS_AHEAD} kun ichidagi oʻyinlar:\n\n"
            "Oʻyin ustiga bosing, tahlil va kuzatish imkoniyati.",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        return

    # ---------- Oʻyin tahlilini koʻrish ----------
    if data.startswith("match_"):
        match_id = int(data.split("_")[1])
        analysis = await get_analysis(match_id)
        if analysis:
            text, added_at = analysis
            added_at_dt = datetime.strptime(added_at, "%Y-%m-%d %H:%M:%S")
            date_str = added_at_dt.strftime("%d.%m.%Y %H:%M")
            msg = f"⚽ **Oʻyin tahlili**\n\n🆔 Match ID: `{match_id}`\n📝 **Tahlil:**\n{text}\n\n🕐 Qoʻshilgan: {date_str}"
        else:
            msg = f"⚽ **Oʻyin tahlili**\n\n🆔 Match ID: `{match_id}`\n📊 Hozircha tahlil mavjud emas."
            if await is_admin(user_id):
                msg += f"\n\n💡 Admin: `/addanalysis {match_id} <tahlil>`"
        
        # Foydalanuvchi obuna bo'lganmi?
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT 1 FROM subscriptions WHERE user_id = ? AND match_id = ?', (user_id, match_id)) as cursor:
                is_subscribed = await cursor.fetchone() is not None
        
        keyboard = build_match_detail_keyboard(match_id, is_subscribed)
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=keyboard)
        return

    # ---------- Obuna bo'lish ----------
    if data.startswith("subscribe_"):
        match_id = int(data.split("_")[1])
        
        # API dan o'yin ma'lumotini olish
        result = await fetch_match_by_id(match_id)
        if "error" in result:
            await query.answer("❌ Xatolik yuz berdi", show_alert=True)
            return
        
        match = result["success"]
        home = match["match"]["homeTeam"]["name"]
        away = match["match"]["awayTeam"]["name"]
        match_time = match["match"]["utcDate"]

        # Bazaga obunani yozish
        await subscribe_user(user_id, match_id, match_time, home, away)

        # Tugmalarni yangilash (holatni o'zgartirish)
        new_keyboard = build_match_detail_keyboard(match_id, is_subscribed=True)
        await query.edit_message_reply_markup(reply_markup=new_keyboard)

        # Qisqa bildirishnoma (popup)
        await query.answer("✅ Kuzatish boshlandi!", show_alert=False)
        return

    # ---------- Obunani bekor qilish ----------
    if data.startswith("unsubscribe_"):
        match_id = int(data.split("_")[1])
        
        # Bazadan obunani o'chirish
        await unsubscribe_user(user_id, match_id)

        # Tugmalarni yangilash (holatni o'zgartirish)
        new_keyboard = build_match_detail_keyboard(match_id, is_subscribed=False)
        await query.edit_message_reply_markup(reply_markup=new_keyboard)

        # Qisqa bildirishnoma
        await query.answer("❌ Kuzatish bekor qilindi", show_alert=False)
        return

# ========== NOTIFIKATSIYA SCHEDULERI ==========
async def notification_scheduler(app: Application):
    """Har daqiqa ishlaydi, obuna boʻlgan oʻyinlar uchun eslatma yuboradi."""
    while True:
        try:
            now = datetime.utcnow()
            subscriptions = await get_all_subscriptions()
            for sub in subscriptions:
                user_id, match_id, match_time_str, home, away, notified_1h, notified_15m, notified_lineups = sub
                match_time = datetime.strptime(match_time_str, "%Y-%m-%dT%H:%M:%SZ")
                delta = match_time - now
                minutes_left = delta.total_seconds() / 60

                # 1 soat qolganda (55-65 daqiqa oralig'i)
                if not notified_1h and 55 <= minutes_left <= 65:
                    await app.bot.send_message(
                        user_id,
                        f"⏰ **1 soat qoldi!**\n\n{home} – {away}\n🕒 {match_time.strftime('%d.%m.%Y %H:%M')} UTC\n\nTarkib eʼlon qilinishi kutilmoqda.",
                        parse_mode="Markdown"
                    )
                    await update_notification_flags(user_id, match_id, one_hour=True)
                    # Tarkib eʼloni simulyatsiyasi
                    if not notified_lineups:
                        await app.bot.send_message(
                            user_id,
                            f"📋 **Asosiy tarkib eʼlon qilindi!**\n\n{home} – {away}\n[Bu yerda haqiqiy tarkib boʻlishi mumkin]",
                            parse_mode="Markdown"
                        )
                        await update_notification_flags(user_id, match_id, lineups=True)

                # 15 daqiqa qolganda (10-20 daqiqa oralig'i)
                if not notified_15m and 10 <= minutes_left <= 20:
                    await app.bot.send_message(
                        user_id,
                        f"⏳ **15 daqiqa qoldi!**\n\n{home} – {away}\n🕒 {match_time.strftime('%d.%m.%Y %H:%M')} UTC",
                        parse_mode="Markdown"
                    )
                    await update_notification_flags(user_id, match_id, fifteen_min=True)

        except Exception as e:
            logger.exception("Notification scheduler error")

        await asyncio.sleep(60)  # har daqiqa tekshiradi

# ========== ADMIN BUYRUQLARI ==========
async def add_analysis_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_admin(user.id):
        await update.message.reply_text("❌ Siz admin emassiz.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("❌ Ishlatish: `/addanalysis 123456 Matn`", parse_mode="Markdown")
        return
    try:
        match_id = int(context.args[0])
        analysis = ' '.join(context.args[1:])
    except:
        await update.message.reply_text("❌ Match ID raqam boʻlishi kerak.")
        return
    await add_analysis(match_id, analysis, user.id)
    await update.message.reply_text(f"✅ Tahlil qoʻshildi (Match ID: {match_id}).")

async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_admin(user.id):
        await update.message.reply_text("❌ Siz admin emassiz.")
        return
    if len(context.args) != 1:
        await update.message.reply_text("❌ Ishlatish: `/addadmin 123456789`", parse_mode="Markdown")
        return
    try:
        new_admin = int(context.args[0])
    except:
        await update.message.reply_text("❌ ID raqam boʻlishi kerak.")
        return
    if await is_admin(new_admin):
        await update.message.reply_text("⚠️ Bu foydalanuvchi allaqachon admin.")
        return
    await add_admin(new_admin, user.id)
    await update.message.reply_text(f"✅ Foydalanuvchi {new_admin} admin qilindi.")

async def remove_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_admin(user.id):
        await update.message.reply_text("❌ Siz admin emassiz.")
        return
    if len(context.args) != 1:
        await update.message.reply_text("❌ Ishlatish: `/removeadmin 123456789`", parse_mode="Markdown")
        return
    try:
        admin_id = int(context.args[0])
    except:
        await update.message.reply_text("❌ ID raqam boʻlishi kerak.")
        return
    if admin_id == 6935090105:
        await update.message.reply_text("❌ Asosiy adminni o‘chirib bo‘lmaydi.")
        return
    if not await is_admin(admin_id):
        await update.message.reply_text("⚠️ Bu foydalanuvchi admin emas.")
        return
    await remove_admin(admin_id)
    await update.message.reply_text(f"✅ Admin {admin_id} olib tashlandi.")

async def list_admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_admin(user.id):
        await update.message.reply_text("❌ Siz admin emassiz.")
        return
    admins = await get_all_admins()
    if not admins:
        await update.message.reply_text("📭 Adminlar ro'yxati bo'sh.")
        return
    text = "👑 **Adminlar:**\n\n"
    for aid, added_by, added_at in admins:
        added_at_dt = datetime.strptime(added_at, "%Y-%m-%d %H:%M:%S")
        text += f"• `{aid}` – qo'shdi: `{added_by}`, {added_at_dt.strftime('%d.%m.%Y')}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

# ========== TEST / DEBUG BUYRUQLARI ==========
async def test_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not FOOTBALL_DATA_KEY:
        await update.message.reply_text("❌ FOOTBALL_DATA_KEY topilmadi!")
        return
    await update.message.reply_text("✅ API kaliti mavjud. `/debug` orqali test qiling.")

async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not FOOTBALL_DATA_KEY:
        await update.message.reply_text("❌ FOOTBALL_DATA_KEY topilmadi!")
        return
    await update.message.reply_text("📊 Debug maʼlumoti: API soʻrovi yuborilmoqda...")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Quyidagi chempionatlardan birini tanlang:",
        reply_markup=get_leagues_keyboard()
    )

# ========== WEB SERVER (RAILWAY UCHUN) ==========
async def health_check(request):
    return web.Response(text="✅ Bot ishlamoqda (Kuzatish tugmasi holati oʻzgaradi)")

async def run_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Web server port {port} da ishga tushdi")

# ========== ASOSIY ==========
async def run_bot():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN topilmadi!")
        return

    # Bazani ishga tushirish
    await init_db()

    application = Application.builder().token(token).build()

    # Handlerlar
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("test", test_api))
    application.add_handler(CommandHandler("debug", debug))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CommandHandler("addanalysis", add_analysis_command))
    application.add_handler(CommandHandler("addadmin", add_admin_command))
    application.add_handler(CommandHandler("removeadmin", remove_admin_command))
    application.add_handler(CommandHandler("listadmins", list_admins_command))

    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    logger.info("🤖 Bot ishga tushdi! (Kuzatish tugmasi holati oʻzgaradi)")

    # Notifikatsiya schedulerini ishga tushirish
    asyncio.create_task(notification_scheduler(application))

    while True:
        await asyncio.sleep(3600)

async def main():
    await asyncio.gather(
        run_web_server(),
        run_bot()
    )

if __name__ == "__main__":
    asyncio.run(main())
