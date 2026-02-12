import os
import asyncio
import logging
import aiohttp
import aiosqlite
from datetime import datetime, timedelta, date
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

TOP_LEAGUES = {
    "PL": {"name": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premyer Liga", "country": "Angliya"},
    "PD": {"name": "🇪🇸 La Liga", "country": "Ispaniya"},
    "SA": {"name": "🇮🇹 Seriya A", "country": "Italiya"},
    "BL1": {"name": "🇩🇪 Bundesliga", "country": "Germaniya"},
    "FL1": {"name": "🇫🇷 Liga 1", "country": "Fransiya"}
}

TRUSTED_SITES = {
    "PL": {
        "base": "https://www.espn.com/soccer/match/_/gameId/{}",
        "bbc": "https://www.bbc.com/sport/football/{}",
        "sky": "https://www.skysports.com/football/{}-vs-{}/{}"
    },
    "PD": {
        "base": "https://www.marca.com/futbol/{}",
        "as": "https://as.com/futbol/{}.html"
    },
    "SA": {
        "base": "https://www.gazzetta.it/calcio/{}",
        "corriere": "https://www.corriere.it/calcio/{}"
    },
    "BL1": {
        "base": "https://www.kicker.de/{}/aufstellung",
        "bild": "https://www.bild.de/sport/fussball/{}.html"
    },
    "FL1": {
        "base": "https://www.lequipe.fr/Football/match/{}",
        "rmc": "https://rmcsport.bfmtv.com/football/{}"
    }
}

DAYS_AHEAD = 7
DB_PATH = "data/bot.db"

# ========== REFERRAL ==========
REFERRAL_BONUS = 2000
MIN_WITHDRAW = 50000
MAX_WITHDRAW_DAILY = 1

# ========== DATABASE ==========
async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_by INTEGER,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS match_analyses (
                match_id INTEGER PRIMARY KEY,
                analysis TEXT NOT NULL,
                added_by INTEGER NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER,
                match_id INTEGER,
                match_time TIMESTAMP NOT NULL,
                home_team TEXT,
                away_team TEXT,
                league_code TEXT,
                notified_1h BOOLEAN DEFAULT 0,
                notified_15m BOOLEAN DEFAULT 0,
                notified_lineups BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, match_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER DEFAULT 0,
                referrer_id INTEGER,
                referral_count INTEGER DEFAULT 0,
                daily_withdraw_date TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (referrer_id) REFERENCES users(user_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL,
                referred_id INTEGER NOT NULL,
                bonus INTEGER DEFAULT 2000,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(referred_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.commit()
    MAIN_ADMIN = 6935090105
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT user_id FROM admins WHERE user_id = ?', (MAIN_ADMIN,)) as cursor:
            if not await cursor.fetchone():
                await db.execute('INSERT INTO admins (user_id, added_by) VALUES (?, ?)', (MAIN_ADMIN, MAIN_ADMIN))
                await db.commit()
                logger.info(f"Asosiy admin qo'shildi: {MAIN_ADMIN}")

# ========== USER FUNCTIONS ==========
async def get_or_create_user(user_id: int, referrer_id: int = None):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)) as cursor:
            user = await cursor.fetchone()
        if not user:
            await db.execute('INSERT INTO users (user_id, referrer_id) VALUES (?, ?)', (user_id, referrer_id))
            await db.commit()
            if referrer_id and referrer_id != user_id:
                async with db.execute('SELECT user_id FROM users WHERE user_id = ?', (referrer_id,)) as cursor:
                    if await cursor.fetchone():
                        await db.execute('UPDATE users SET balance = balance + ?, referral_count = referral_count + 1 WHERE user_id = ?', (REFERRAL_BONUS, referrer_id))
                        await db.execute('INSERT OR IGNORE INTO referrals (referrer_id, referred_id, bonus) VALUES (?, ?, ?)', (referrer_id, user_id, REFERRAL_BONUS))
                        await db.commit()
                        logger.info(f"Referal bonus: {referrer_id} +{REFERRAL_BONUS} (yangi {user_id})")
            async with db.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)) as cursor:
                user = await cursor.fetchone()
        return user

async def get_user_balance(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def can_withdraw(user_id: int) -> tuple:
    balance = await get_user_balance(user_id)
    if balance < MIN_WITHDRAW:
        return False, f"❌ Minimal yechish miqdori {MIN_WITHDRAW:,} soʻm. Sizda {balance:,} soʻm bor."
    today_str = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT daily_withdraw_date FROM users WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row and row[0] == today_str:
                return False, "❌ Bugun siz allaqachon pul yechib boʻlgansiz. Ertaga qayta urinib koʻring."
    return True, ""

async def register_withdraw(user_id: int, amount: int) -> bool:
    can, msg = await can_withdraw(user_id)
    if not can:
        return False
    if amount > await get_user_balance(user_id):
        return False
    today_str = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE users SET balance = balance - ?, daily_withdraw_date = ? WHERE user_id = ?', (amount, today_str, user_id))
        await db.execute('INSERT INTO withdrawals (user_id, amount, status) VALUES (?, ?, ?)', (user_id, amount, 'completed'))
        await db.commit()
    return True

async def get_referral_link(user_id: int, bot_username: str) -> str:
    return f"https://t.me/{bot_username}?start=ref_{user_id}"

async def get_referral_stats(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT referral_count FROM users WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            count = row[0] if row else 0
        async with db.execute('SELECT SUM(bonus) FROM referrals WHERE referrer_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            total_bonus = row[0] if row[0] else 0
        async with db.execute('SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND DATE(created_at) = DATE("now")', (user_id,)) as cursor:
            row = await cursor.fetchone()
            today_count = row[0] if row else 0
    return {"count": count, "total_bonus": total_bonus, "today_count": today_count}

# ========== ADMIN ==========
async def is_admin(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT 1 FROM admins WHERE user_id = ?', (user_id,)) as cursor:
            return await cursor.fetchone() is not None

async def add_admin(user_id: int, added_by: int) -> bool:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('INSERT INTO admins (user_id, added_by) VALUES (?, ?)', (user_id, added_by))
            await db.commit()
            return True
    except:
        return False

async def remove_admin(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM admins WHERE user_id = ?', (user_id,))
        await db.commit()
        return True

async def get_all_admins():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT user_id, added_by, added_at FROM admins ORDER BY added_at') as cursor:
            return await cursor.fetchall()

# ========== ANALYSIS ==========
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

async def get_analysis(match_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT analysis, added_at FROM match_analyses WHERE match_id = ?', (match_id,)) as cursor:
            return await cursor.fetchone()

# ========== SUBSCRIPTIONS ==========
async def subscribe_user(user_id: int, match_id: int, match_time: str, home: str, away: str, league_code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT OR REPLACE INTO subscriptions 
            (user_id, match_id, match_time, home_team, away_team, league_code, notified_1h, notified_15m, notified_lineups)
            VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0)
        ''', (user_id, match_id, match_time, home, away, league_code))
        await db.commit()

async def unsubscribe_user(user_id: int, match_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM subscriptions WHERE user_id = ? AND match_id = ?', (user_id, match_id))
        await db.commit()

async def get_all_subscriptions():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('''
            SELECT user_id, match_id, match_time, home_team, away_team, league_code, 
                   notified_1h, notified_15m, notified_lineups 
            FROM subscriptions
        ''') as cursor:
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

# ========== YANGI QO‘SHIMCHA: O‘YIN OBUNACHILARINI OLISH ==========
async def get_subscribers_for_match(match_id: int):
    """Berilgan match_id ga obuna bo‘lgan foydalanuvchilar ro‘yxatini qaytaradi"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT user_id FROM subscriptions WHERE match_id = ?', (match_id,)) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

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

# ========== LINEUPS ==========
async def fetch_match_lineups(match_id: int):
    result = await fetch_match_by_id(match_id)
    if "error" in result:
        return None
    match = result["success"]
    home_lineup = match.get("homeTeam", {}).get("lineup", [])
    away_lineup = match.get("awayTeam", {}).get("lineup", [])
    return {
        "home_team": match.get("homeTeam", {}).get("name", "Noma'lum"),
        "away_team": match.get("awayTeam", {}).get("name", "Noma'lum"),
        "home_lineup": home_lineup,
        "away_lineup": away_lineup,
        "home_coach": match.get("homeTeam", {}).get("coach", {}).get("name") if match.get("homeTeam", {}).get("coach") else None,
        "away_coach": match.get("awayTeam", {}).get("coach", {}).get("name") if match.get("awayTeam", {}).get("coach") else None,
        "home_formation": match.get("homeTeam", {}).get("formation"),
        "away_formation": match.get("awayTeam", {}).get("formation"),
        "venue": match.get("venue"),
        "attendance": match.get("attendance")
    }

def format_lineups_message(lineups_data):
    if not lineups_data:
        return "📋 Tarkiblar hali e'lon qilinmagan."
    msg = f"⚽ **{lineups_data['home_team']} vs {lineups_data['away_team']}**\n\n"
    if lineups_data.get('venue'):
        msg += f"🏟️ Stadion: {lineups_data['venue']}\n"
    if lineups_data.get('attendance'):
        msg += f"👥 Tomoshabin: {lineups_data['attendance']}\n"
    msg += "\n"
    msg += f"🏠 **{lineups_data['home_team']}**"
    if lineups_data.get('home_formation'):
        msg += f" ({lineups_data['home_formation']})"
    if lineups_data.get('home_coach'):
        msg += f" – Murabbiy: {lineups_data['home_coach']}"
    msg += "\n" + "━" * 30 + "\n"
    if lineups_data['home_lineup']:
        for player in lineups_data['home_lineup'][:11]:
            name = player.get('name', 'Noma\'lum')
            position = player.get('position', '')
            shirt = player.get('shirtNumber', '')
            position_icon = "🥅" if "Goalkeeper" in position else "🛡️" if "Defender" in position else "⚡" if "Midfielder" in position else "🎯"
            msg += f"{position_icon} {shirt} – {name} ({position})\n"
    else:
        msg += "❌ Tarkib e'lon qilinmagan\n"
    msg += "\n"
    msg += f"🛣️ **{lineups_data['away_team']}**"
    if lineups_data.get('away_formation'):
        msg += f" ({lineups_data['away_formation']})"
    if lineups_data.get('away_coach'):
        msg += f" – Murabbiy: {lineups_data['away_coach']}"
    msg += "\n" + "━" * 30 + "\n"
    if lineups_data['away_lineup']:
        for player in lineups_data['away_lineup'][:11]:
            name = player.get('name', 'Noma\'lum')
            position = player.get('position', '')
            shirt = player.get('shirtNumber', '')
            position_icon = "🥅" if "Goalkeeper" in position else "🛡️" if "Defender" in position else "⚡" if "Midfielder" in position else "🎯"
            msg += f"{position_icon} {shirt} – {name} ({position})\n"
    else:
        msg += "❌ Tarkib e'lon qilinmagan\n"
    return msg

def generate_match_links(match_id: int, home_team: str, away_team: str, league_code: str):
    links = []
    espn_url = f"https://www.espn.com/soccer/match/_/gameId/{match_id}"
    links.append(("📺 ESPN", espn_url))
    if league_code == "PL":
        bbc_url = f"https://www.bbc.com/sport/football/{match_id}"
        links.append(("📰 BBC Sport", bbc_url))
    sky_url = f"https://www.skysports.com/football/{home_team.lower().replace(' ', '-')}-vs-{away_team.lower().replace(' ', '-')}/{match_id}"
    links.append(("⚡ Sky Sports", sky_url))
    if league_code == "PD":
        marca_url = f"https://www.marca.com/futbol/primera-division/{match_id}.html"
        as_url = f"https://as.com/futbol/primera/{match_id}.html"
        links.append(("📘 MARCA", marca_url))
        links.append(("📙 AS", as_url))
    elif league_code == "SA":
        gazzetta_url = f"https://www.gazzetta.it/calcio/serie-a/match-{match_id}.shtml"
        corriere_url = f"https://www.corriere.it/calcio/serie-a/{match_id}.shtml"
        links.append(("📗 La Gazzetta", gazzetta_url))
        links.append(("📕 Corriere", corriere_url))
    elif league_code == "BL1":
        kicker_url = f"https://www.kicker.de/{match_id}/aufstellung"
        bild_url = f"https://www.bild.de/sport/fussball/bundesliga/{match_id}.html"
        links.append(("📘 Kicker", kicker_url))
        links.append(("📙 Bild", bild_url))
    elif league_code == "FL1":
        lequipe_url = f"https://www.lequipe.fr/Football/match/{match_id}"
        rmc_url = f"https://rmcsport.bfmtv.com/football/match-{match_id}.html"
        links.append(("📗 L'Equipe", lequipe_url))
        links.append(("📕 RMC Sport", rmc_url))
    flashscore_url = f"https://www.flashscore.com/match/{match_id}/#/lineups"
    links.append(("⚽ FlashScore", flashscore_url))
    sofascore_url = f"https://www.sofascore.com/football/match/{match_id}"
    links.append(("📊 SofaScore", sofascore_url))
    return links

def format_links_message(links):
    msg = "🔗 **Ishonchli saytlarda tarkiblarni ko‘ring:**\n\n"
    for name, url in links[:5]:
        msg += f"• [{name}]({url})\n"
    return msg

# ========== INLINE KEYBOARDS ==========
def money_row():
    """Pul ishlash tugmalari qatori (barcha klaviaturaga qo‘shiladi)"""
    return [
        InlineKeyboardButton("💰 Pul ishlash", callback_data="money_info"),
        InlineKeyboardButton("💳 Balans", callback_data="balance_info"),
        InlineKeyboardButton("💸 Pul yechish", callback_data="withdraw_info")
    ]

def get_leagues_keyboard():
    keyboard = []
    for code, data in TOP_LEAGUES.items():
        keyboard.append([InlineKeyboardButton(data["name"], callback_data=f"league_{code}")])
    keyboard.append(money_row())
    return InlineKeyboardMarkup(keyboard)

def build_matches_keyboard(matches):
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
    keyboard.append(money_row())
    return InlineKeyboardMarkup(keyboard)

def build_match_detail_keyboard(match_id: int, is_subscribed: bool = False, lineups_available: bool = False):
    keyboard = []
    if is_subscribed:
        keyboard.append([InlineKeyboardButton("🔕 Kuzatishni bekor qilish", callback_data=f"unsubscribe_{match_id}")])
    else:
        keyboard.append([InlineKeyboardButton("🔔 Kuzatish", callback_data=f"subscribe_{match_id}")])
    keyboard.append([
        InlineKeyboardButton("📰 Futbol yangiliklari", url="https://t.me/ai_futinside"),
        InlineKeyboardButton("📊 Chuqur tahlil", url="https://futbolinside.netlify.app/"),
        InlineKeyboardButton("🎲 Stavka qilish", url="https://superlative-twilight-47ef34.netlify.app/")
    ])
    if lineups_available:
        keyboard.append([InlineKeyboardButton("📋 Tarkiblarni ko‘rish", callback_data=f"lineups_{match_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Back to Leagues", callback_data="leagues")])
    keyboard.append(money_row())
    return InlineKeyboardMarkup(keyboard)

# ========== HANDLERS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    args = context.args
    referrer_id = None
    if args and args[0].startswith("ref_"):
        try:
            referrer_id = int(args[0].replace("ref_", ""))
            if referrer_id == user_id:
                referrer_id = None
        except:
            referrer_id = None
    await get_or_create_user(user_id, referrer_id)
    bot_username = (await context.bot.get_me()).username
    referral_link = await get_referral_link(user_id, bot_username)
    welcome_text = (
        f"👋 Assalomu alaykum, {user.first_name}!\n\n"
        f"⚽ Ushbu bot orqali top 5 chempionat oʻyinlarini kuzatishingiz, "
        f"tahlillarni olishingiz va oʻyinlar haqida eslatmalarni sozlashingiz mumkin.\n\n"
        f"💰 **Pul ishlash imkoniyati**:\n"
        f"Doʻstlaringizni taklif qiling va har bir taklif uchun **{REFERRAL_BONUS:,} soʻm** oling!\n"
        f"Sizning referal havolangiz:\n`{referral_link}`\n\n"
        f"💸 Minimal pul yechish: **{MIN_WITHDRAW:,} soʻm**, kuniga **1 marta**.\n\n"
        f"Quyida ligalardan birini tanlang:"
    )
    await update.message.reply_text(
        welcome_text,
        parse_mode="Markdown",
        reply_markup=get_leagues_keyboard()
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    # ---------- PUL ISHLASH INFO (YANGI XABAR) + SHARE TUGMASI ----------
    if data == "money_info":
        bot_username = (await context.bot.get_me()).username
        referral_link = await get_referral_link(user_id, bot_username)
        stats = await get_referral_stats(user_id)
        balance = await get_user_balance(user_id)
        text = (
            f"💰 **Pul ishlash tizimi**\n\n"
            f"• Har bir doʻstingizni taklif qilish uchun: **+{REFERRAL_BONUS:,} soʻm**\n"
            f"• Minimal pul yechish: **{MIN_WITHDRAW:,} soʻm**\n"
            f"• Kuniga **1 marta** pul yechish mumkin.\n\n"
            f"📊 **Sizning statistika:**\n"
            f"• Balans: **{balance:,} soʻm**\n"
            f"• Taklif qilinganlar: **{stats['count']} ta**\n"
            f"• Bugun taklif qilingan: **{stats['today_count']} ta**\n"
            f"• Jami bonus: **{stats['total_bonus']:,} soʻm**\n\n"
            f"🔗 **Sizning referal havolangiz:**\n`{referral_link}`\n\n"
            f"⚠️ Doʻstingiz botga start bosganida bonus avtomatik hisoblanadi."
        )

        # 📤 Share tugmasi uchun maxsus xabar va havola
        share_text = (
            f"🤖 Futbol tahlillari va pul ishlash botiga taklif!\n\n"
            f"Bot orqali top-5 chempionat oʻyinlarini kuzating, tahlillarni oling va "
            f"doʻstlaringizni taklif qilib pul ishlang.\n\n"
            f"🎁 Har bir taklif uchun +{REFERRAL_BONUS:,} soʻm bonus!\n"
            f"👇 Quyidagi havola orqali botga oʻting:\n{referral_link}"
        )
        from urllib.parse import quote
        share_url = f"https://t.me/share/url?url={quote(referral_link)}&text={quote(share_text)}"

        keyboard = [
            [InlineKeyboardButton("📤 Do'stlarga yuborish", url=share_url)],
            [InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_to_start")],
            money_row()
        ]
        back_keyboard = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(text, parse_mode="Markdown", reply_markup=back_keyboard)
        return

    # ---------- BALANS KO'RISH ----------
    if data == "balance_info":
        balance = await get_user_balance(user_id)
        stats = await get_referral_stats(user_id)
        text = (
            f"💳 **Sizning balansingiz**\n\n"
            f"💰 Balans: **{balance:,} soʻm**\n"
            f"👥 Referallar: **{stats['count']} ta**\n"
            f"🎁 Bonus: **{stats['total_bonus']:,} soʻm**\n\n"
            f"💸 Pul yechish uchun minimal miqdor: **{MIN_WITHDRAW:,} soʻm**\n"
            f"📅 Kuniga **1 marta** yechish mumkin."
        )
        back_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_to_start")],
            money_row()
        ])
        await query.message.reply_text(text, parse_mode="Markdown", reply_markup=back_keyboard)
        return

    # ---------- PUL YECHISH ----------
    if data == "withdraw_info":
        balance = await get_user_balance(user_id)
        if balance < MIN_WITHDRAW:
            text = f"❌ Sizda yetarli mablagʻ yoʻq.\nBalans: **{balance:,} soʻm**\nMinimal yechish: **{MIN_WITHDRAW:,} soʻm**\n\nDoʻstlaringizni taklif qilib pul ishlang!"
            back_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_to_start")],
                money_row()
            ])
            await query.message.reply_text(text, parse_mode="Markdown", reply_markup=back_keyboard)
            return
        can, msg = await can_withdraw(user_id)
        if not can:
            back_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_to_start")],
                money_row()
            ])
            await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=back_keyboard)
            return
        success = await register_withdraw(user_id, MIN_WITHDRAW)
        if success:
            withdraw_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("💸 Pul yechish (test)", url="https://futbolinsidepulyechish.netlify.app/")],
                [InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_to_start")],
                money_row()
            ])
            await query.message.reply_text(
                f"✅ **Pul yechish soʻrovingiz qabul qilindi!**\n\n"
                f"Yechilgan miqdor: **{MIN_WITHDRAW:,} soʻm**\n"
                f"Qolgan balans: **{balance - MIN_WITHDRAW:,} soʻm**\n\n"
                f"⚠️ Bu test rejimi. Pul yechish uchun quyidagi havolaga oʻting:",
                parse_mode="Markdown",
                reply_markup=withdraw_keyboard
            )
        else:
            back_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_to_start")],
                money_row()
            ])
            await query.message.reply_text("❌ Xatolik yuz berdi. Qayta urinib koʻring.", reply_markup=back_keyboard)
        return

    # ---------- BOSH MENYU ----------
    if data == "back_to_start":
        user = update.effective_user
        await get_or_create_user(user_id, None)
        bot_username = (await context.bot.get_me()).username
        referral_link = await get_referral_link(user_id, bot_username)
        welcome_text = (
            f"👋 Assalomu alaykum, {user.first_name}!\n\n"
            f"⚽ Ushbu bot orqali top 5 chempionat oʻyinlarini kuzatishingiz, "
            f"tahlillarni olishingiz va oʻyinlar haqida eslatmalarni sozlashingiz mumkin.\n\n"
            f"💰 **Pul ishlash imkoniyati**:\n"
            f"Doʻstlaringizni taklif qiling va har bir taklif uchun **{REFERRAL_BONUS:,} soʻm** oling!\n"
            f"Sizning referal havolangiz:\n`{referral_link}`\n\n"
            f"💸 Minimal pul yechish: **{MIN_WITHDRAW:,} soʻm**, kuniga **1 marta**.\n\n"
            f"Quyida ligalardan birini tanlang:"
        )
        await query.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=get_leagues_keyboard())
        return

    # ---------- FUTBOL QISMI (LIGALAR, OʻYINLAR, KUZATISH) ----------
    if data == "leagues":
        await query.edit_message_text(
            "sport uchun eng yuqori sifatdagi taxlilarni olish uchun Quyidagi chempionatlardan birini tanlang:",
            reply_markup=get_leagues_keyboard()
        )
        return

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

    if data.startswith("match_"):
        match_id = int(data.split("_")[1])
        analysis = await get_analysis(match_id)
        match_result = await fetch_match_by_id(match_id)
        league_code = "PL"
        home_team = "Noma'lum"
        away_team = "Noma'lum"
        if "success" in match_result:
            match_data = match_result["success"]
            competition = match_data.get("competition", {}).get("code", "")
            if competition in TOP_LEAGUES:
                league_code = competition
            home_team = match_data.get("homeTeam", {}).get("name", "Noma'lum")
            away_team = match_data.get("awayTeam", {}).get("name", "Noma'lum")
        if analysis:
            text, added_at = analysis
            added_at_dt = datetime.strptime(added_at, "%Y-%m-%d %H:%M:%S")
            date_str = added_at_dt.strftime("%d.%m.%Y %H:%M")
            msg = f"⚽ **Oʻyin tahlili**\n\n🆔 Match ID: `{match_id}`\n📝 **Tahlil:**\n{text}\n\n🕐 Qoʻshilgan: {date_str}"
        else:
            msg = f"⚽ **Oʻyin tahlili**\n\n🆔 Match ID: `{match_id}`\n📊 Hozircha tahlil mavjud emas."
            if await is_admin(user_id):
                msg += f"\n\n💡 Admin: `/addanalysis {match_id} <tahlil>`"
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT 1 FROM subscriptions WHERE user_id = ? AND match_id = ?', (user_id, match_id)) as cursor:
                is_subscribed = await cursor.fetchone() is not None
        lineups_data = await fetch_match_lineups(match_id)
        lineups_available = lineups_data and (lineups_data['home_lineup'] or lineups_data['away_lineup'])
        keyboard = build_match_detail_keyboard(match_id, is_subscribed, lineups_available)
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=keyboard)
        return

    if data.startswith("lineups_"):
        match_id = int(data.split("_")[1])
        await query.edit_message_text("⏳ Tarkiblar yuklanmoqda...")
        lineups_data = await fetch_match_lineups(match_id)
        if lineups_data and (lineups_data['home_lineup'] or lineups_data['away_lineup']):
            msg = format_lineups_message(lineups_data)
        else:
            msg = "❌ Bu oʻyin uchun tarkiblar hali eʼlon qilinmagan."
        match_result = await fetch_match_by_id(match_id)
        league_code = "PL"
        home_team = "Noma'lum"
        away_team = "Noma'lum"
        if "success" in match_result:
            match_data = match_result["success"]
            competition = match_data.get("competition", {}).get("code", "")
            if competition in TOP_LEAGUES:
                league_code = competition
            home_team = match_data.get("homeTeam", {}).get("name", "Noma'lum")
            away_team = match_data.get("awayTeam", {}).get("name", "Noma'lum")
        links = generate_match_links(match_id, home_team, away_team, league_code)
        msg += "\n\n" + format_links_message(links)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Oʻyinga qaytish", callback_data=f"match_{match_id}")],
            [InlineKeyboardButton("🔙 Back to Leagues", callback_data="leagues")],
            money_row()
        ])
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=keyboard)
        return

    if data.startswith("subscribe_"):
        match_id = int(data.split("_")[1])
        result = await fetch_match_by_id(match_id)
        if "error" in result:
            await query.answer("❌ Xatolik yuz berdi", show_alert=True)
            return
        match = result["success"]
        home = match["homeTeam"]["name"]
        away = match["awayTeam"]["name"]
        match_time = match["utcDate"]
        competition = match.get("competition", {}).get("code", "")
        league_code = competition if competition in TOP_LEAGUES else "PL"
        await subscribe_user(user_id, match_id, match_time, home, away, league_code)
        new_keyboard = build_match_detail_keyboard(match_id, is_subscribed=True)
        await query.edit_message_reply_markup(reply_markup=new_keyboard)
        await query.answer("✅ Kuzatish boshlandi!", show_alert=False)
        return

    if data.startswith("unsubscribe_"):
        match_id = int(data.split("_")[1])
        await unsubscribe_user(user_id, match_id)
        new_keyboard = build_match_detail_keyboard(match_id, is_subscribed=False)
        await query.edit_message_reply_markup(reply_markup=new_keyboard)
        await query.answer("❌ Kuzatish bekor qilindi", show_alert=False)
        return

# ========== NOTIFICATION SCHEDULER ==========
async def notification_scheduler(app: Application):
    while True:
        try:
            now = datetime.utcnow()
            subscriptions = await get_all_subscriptions()
            for sub in subscriptions:
                user_id, match_id, match_time_str, home, away, league_code, notified_1h, notified_15m, notified_lineups = sub
                match_time = datetime.strptime(match_time_str, "%Y-%m-%dT%H:%M:%SZ")
                delta = match_time - now
                minutes_left = delta.total_seconds() / 60
                if not notified_1h and 55 <= minutes_left <= 65:
                    await app.bot.send_message(
                        user_id,
                        f"⏰ **1 soat qoldi!**\n\n{home} – {away}\n🕒 {match_time.strftime('%d.%m.%Y %H:%M')} UTC+0\n\n📋 Tarkiblar eʼlon qilinishi kutilmoqda.",
                        parse_mode="Markdown"
                    )
                    if not notified_lineups:
                        lineups_data = await fetch_match_lineups(match_id)
                        if lineups_data and (lineups_data['home_lineup'] or lineups_data['away_lineup']):
                            lineup_msg = format_lineups_message(lineups_data)
                            await app.bot.send_message(user_id, lineup_msg, parse_mode="Markdown")
                            links = generate_match_links(match_id, home, away, league_code)
                            links_msg = format_links_message(links)
                            await app.bot.send_message(user_id, links_msg, parse_mode="Markdown", disable_web_page_preview=True)
                        else:
                            links = generate_match_links(match_id, home, away, league_code)
                            msg = f"📋 **{home} – {away}**\n\n"
                            msg += "❌ Tarkiblar API orqali e'lon qilinmagan.\n"
                            msg += "🔗 Quyidagi ishonchli saytlarda tarkiblarni ko‘ring:\n\n"
                            for name, url in links[:4]:
                                msg += f"• [{name}]({url})\n"
                            await app.bot.send_message(user_id, msg, parse_mode="Markdown", disable_web_page_preview=True)
                        await update_notification_flags(user_id, match_id, lineups=True)
                    await update_notification_flags(user_id, match_id, one_hour=True)
                if not notified_15m and 10 <= minutes_left <= 20:
                    links = generate_match_links(match_id, home, away, league_code)
                    msg = f"⏳ **15 daqiqa qoldi!**\n\n{home} – {away}\n🕒 {match_time.strftime('%d.%m.%Y %H:%M')} UTC+0\n\n"
                    msg += "🔗 Jonli tarkiblar va statistika:\n\n"
                    for name, url in links[:5]:
                        msg += f"• [{name}]({url})\n"
                    await app.bot.send_message(user_id, msg, parse_mode="Markdown", disable_web_page_preview=True)
                    await update_notification_flags(user_id, match_id, fifteen_min=True)
        except Exception as e:
            logger.exception(f"Notification scheduler error: {e}")
        await asyncio.sleep(60)

# ========== ADMIN COMMANDS ==========
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

    # Tahlilni saqlash (yangi yoki yangilash)
    await add_analysis(match_id, analysis, user.id)
    await update.message.reply_text(f"✅ Tahlil qoʻshildi (Match ID: {match_id}).")

    # ------------------- BILDIRISHNOMA QISMI -------------------
    # Shu o‘yinga obuna bo‘lgan foydalanuvchilarni olish
    subscribers = await get_subscribers_for_match(match_id)
    if subscribers:
        sent_count = 0
        for uid in subscribers:
            try:
                # Adminning o‘ziga ham yuboriladi (agar obuna bo‘lsa) – bu istalgan holat
                await context.bot.send_message(
                    uid,
                    f"📝 **Oʻyin tahlili yangilandi!**\n\n"
                    f"🆔 Match ID: `{match_id}`\n"
                    f"📊 **Yangi tahlil:**\n{analysis}\n\n"
                    f"👇 Tahlilni ko‘rish uchun bosing:",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📋 Tahlilni ko‘rish", callback_data=f"match_{match_id}")]
                    ])
                )
                sent_count += 1
            except Exception as e:
                logger.error(f"Tahlil bildirishnomasini yuborib boʻlmadi (user {uid}): {e}")
        await update.message.reply_text(f"📢 {sent_count} ta obunachiga bildirishnoma yuborildi.")
    # ------------------------------------------------------------

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

async def admin_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_admin(user.id):
        await update.message.reply_text("❌ Siz admin emassiz.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT COUNT(*) FROM users') as cursor:
            total_users = (await cursor.fetchone())[0]
        async with db.execute('SELECT COUNT(*) FROM referrals') as cursor:
            total_refs = (await cursor.fetchone())[0]
        async with db.execute('SELECT SUM(balance) FROM users') as cursor:
            total_balance = (await cursor.fetchone())[0] or 0
        async with db.execute('SELECT COUNT(*) FROM withdrawals WHERE status="completed"') as cursor:
            total_withdrawals = (await cursor.fetchone())[0]
        async with db.execute('SELECT SUM(amount) FROM withdrawals WHERE status="completed"') as cursor:
            total_withdrawn = (await cursor.fetchone())[0] or 0
    text = (
        f"📊 **Bot statistikasi**\n\n"
        f"👥 Foydalanuvchilar: {total_users}\n"
        f"🔗 Referallar: {total_refs}\n"
        f"💰 Jami balans: {total_balance:,} soʻm\n"
        f"💸 Yechimlar soni: {total_withdrawals}\n"
        f"💵 Jami yechilgan: {total_withdrawn:,} soʻm"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def test_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not FOOTBALL_DATA_KEY:
        await update.message.reply_text("❌ FOOTBALL_DATA_KEY topilmadi!")
        return
    await update.message.reply_text("✅ API kaliti mavjud.")

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

# ========== WEB SERVER ==========
async def health_check(request):
    return web.Response(text="✅ Bot ishlamoqda (Futbol + Pul + Share + Bildirishnoma)")

async def run_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Web server port {port} da ishga tushdi")

# ========== MAIN ==========
async def run_bot():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN topilmadi!")
        return
    await init_db()
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("test", test_api))
    application.add_handler(CommandHandler("debug", debug))
    application.add_handler(CommandHandler("stats", admin_stats_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CommandHandler("addanalysis", add_analysis_command))
    application.add_handler(CommandHandler("addadmin", add_admin_command))
    application.add_handler(CommandHandler("removeadmin", remove_admin_command))
    application.add_handler(CommandHandler("listadmins", list_admins_command))
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    logger.info("🤖 Bot ishga tushdi! (Futbol + Pul + Share + Bildirishnoma)")
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
