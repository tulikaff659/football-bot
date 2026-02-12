import os
import asyncio
import logging
import aiohttp
import aiosqlite
import random
import time
import re
from datetime import datetime, timedelta, date
from aiohttp import web
from urllib.parse import quote
from collections import OrderedDict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.helpers import escape_markdown

# ---------- SOZLAMALAR ----------
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
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

DAYS_AHEAD = 7
DB_PATH = "data/bot.db"

# ========== REFERRAL & BONUS ==========
REFERRAL_BONUS = 2000
MIN_WITHDRAW = 50000
MAX_WITHDRAW_DAILY = 1
AISPORTS_BONUS = 30000

# ========== API RATE LIMIT ==========
API_SEMAPHORE = asyncio.Semaphore(1)
API_LAST_CALL = 0
API_MIN_INTERVAL = 6

async def rate_limited_api_call(url, headers, params=None):
    global API_LAST_CALL
    async with API_SEMAPHORE:
        now = time.time()
        if now - API_LAST_CALL < API_MIN_INTERVAL:
            await asyncio.sleep(API_MIN_INTERVAL - (now - API_LAST_CALL))
        for attempt in range(3):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=headers, params=params) as resp:
                        API_LAST_CALL = time.time()
                        if resp.status == 200:
                            return {"success": await resp.json()}
                        elif resp.status == 429:
                            await asyncio.sleep(2 ** attempt + random.uniform(1, 3))
                        else:
                            return {"error": f"❌ API xatolik: {resp.status}"}
            except Exception as e:
                logger.error(f"API call xatosi (urinish {attempt+1}): {e}")
                await asyncio.sleep(2 ** attempt)
        return {"error": "❌ API ga bogʻlanib boʻlmadi"}

# ========== MATCH CACHE (10 daqiqa) ==========
match_cache = OrderedDict()
CACHE_TTL = 600

async def get_cached_match(match_id: int):
    now = time.time()
    if match_id in match_cache:
        data, ts = match_cache[match_id]
        if now - ts < CACHE_TTL:
            return data
        del match_cache[match_id]
    url = f"{FOOTBALL_DATA_URL}/matches/{match_id}"
    headers = {"X-Auth-Token": FOOTBALL_DATA_KEY}
    result = await rate_limited_api_call(url, headers)
    if "success" in result:
        match_cache[match_id] = (result["success"], now)
        return result["success"]
    return None

# ========== DATABASE ==========
async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY, added_by INTEGER, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("""CREATE TABLE IF NOT EXISTS match_analyses (
            match_id INTEGER PRIMARY KEY, 
            analysis TEXT NOT NULL DEFAULT 'Tahlil kutilmoqda', 
            analysis_url TEXT,
            added_by INTEGER NOT NULL, 
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS subscriptions (
            user_id INTEGER, match_id INTEGER, match_time TIMESTAMP NOT NULL, home_team TEXT, away_team TEXT, league_code TEXT,
            notified_1h BOOLEAN DEFAULT 0, notified_15m BOOLEAN DEFAULT 0, notified_lineups BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, match_id))""")
        await db.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 0, referrer_id INTEGER,
            referral_count INTEGER DEFAULT 0, daily_withdraw_date TEXT, aisports_bonus_received INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (referrer_id) REFERENCES users(user_id))""")
        try:
            await db.execute("ALTER TABLE match_analyses ADD COLUMN analysis_url TEXT")
        except:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN aisports_bonus_received INTEGER DEFAULT 0")
        except:
            pass
        await db.execute("CREATE TABLE IF NOT EXISTS referrals (id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER NOT NULL, referred_id INTEGER NOT NULL, bonus INTEGER DEFAULT 2000, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(referred_id))")
        await db.execute("CREATE TABLE IF NOT EXISTS withdrawals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount INTEGER NOT NULL, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.commit()
    MAIN_ADMIN = 6935090105
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM admins WHERE user_id = ?", (MAIN_ADMIN,)) as cur:
            if not await cur.fetchone():
                await db.execute("INSERT INTO admins (user_id, added_by) VALUES (?, ?)", (MAIN_ADMIN, MAIN_ADMIN))
                await db.commit()
                logger.info(f"Asosiy admin qo'shildi: {MAIN_ADMIN}")

# ========== USER FUNCTIONS ==========
async def get_or_create_user(user_id: int, referrer_id: int = None, bot=None, referred_name=None):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            user = await cur.fetchone()
        if not user:
            await db.execute("INSERT INTO users (user_id, referrer_id, aisports_bonus_received) VALUES (?, ?, 0)", (user_id, referrer_id))
            await db.commit()
            if referrer_id and referrer_id != user_id:
                async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (referrer_id,)) as cur:
                    if await cur.fetchone():
                        await db.execute("UPDATE users SET balance = balance + ?, referral_count = referral_count + 1 WHERE user_id = ?", (REFERRAL_BONUS, referrer_id))
                        await db.execute("INSERT OR IGNORE INTO referrals (referrer_id, referred_id, bonus) VALUES (?, ?, ?)", (referrer_id, user_id, REFERRAL_BONUS))
                        await db.commit()
                        if bot and referred_name:
                            asyncio.create_task(send_referral_notification(referrer_id, referred_name, REFERRAL_BONUS, bot))
            async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
                user = await cur.fetchone()
    return user

async def send_referral_notification(referrer_id: int, referred_name: str, bonus: int, bot):
    try:
        await bot.send_message(referrer_id,
            f"🎉 **Tabriklaymiz!**\n\nSizning taklif havolangiz orqali {referred_name} botga qoʻshildi.\n💰 Hisobingizga **{bonus:,} soʻm** bonus qoʻshildi!\n\n📊 Doʻstlaringizni koʻproq taklif qilib pul ishlang.",
            parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Referal xabar yuborilmadi ({referrer_id}): {e}")

async def get_user_balance(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

async def can_withdraw(user_id: int):
    balance = await get_user_balance(user_id)
    if balance < MIN_WITHDRAW:
        return False, f"❌ Minimal yechish miqdori {MIN_WITHDRAW:,} soʻm. Sizda {balance:,} soʻm bor."
    today_str = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT daily_withdraw_date FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            if row and row[0] == today_str:
                return False, "❌ Bugun siz allaqachon pul yechib boʻlgansiz. Ertaga qayta urinib koʻring."
    return True, ""

async def register_withdraw(user_id: int, amount: int) -> bool:
    can, msg = await can_withdraw(user_id)
    if not can: return False
    if amount > await get_user_balance(user_id): return False
    today_str = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance - ?, daily_withdraw_date = ? WHERE user_id = ?", (amount, today_str, user_id))
        await db.execute("INSERT INTO withdrawals (user_id, amount, status) VALUES (?, ?, ?)", (user_id, amount, 'completed'))
        await db.commit()
    return True

async def get_referral_link(user_id: int, bot_username: str) -> str:
    return f"https://t.me/{bot_username}?start=ref_{user_id}"

async def get_referral_stats(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT referral_count FROM users WHERE user_id = ?", (user_id,)) as cur:
            cnt = (await cur.fetchone())[0] or 0
        async with db.execute("SELECT SUM(bonus) FROM referrals WHERE referrer_id = ?", (user_id,)) as cur:
            total = (await cur.fetchone())[0] or 0
        async with db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND DATE(created_at) = DATE('now')", (user_id,)) as cur:
            today = (await cur.fetchone())[0] or 0
        return {"count": cnt, "total_bonus": total, "today_count": today}

# ========== AISPORTS BONUS ==========
async def give_aisports_bonus(user_id: int, bot):
    await asyncio.sleep(random.randint(60, 120))
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT aisports_bonus_received FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            if not row or row[0] == 1:
                return
        await db.execute("UPDATE users SET balance = balance + ?, aisports_bonus_received = 1 WHERE user_id = ?", (AISPORTS_BONUS, user_id))
        await db.commit()
    try:
        await bot.send_message(user_id,
            f"🎁 **30 000 soʻm aisports dan bonus puli hisobingizga qoʻshildi!**\n\n💰 Yangi balans: {await get_user_balance(user_id):,} soʻm\n\n📊 Doʻstlaringizni taklif qilib yana pul ishlashingiz mumkin.",
            parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Aisports bonus xabari yuborilmadi ({user_id}): {e}")

async def schedule_aisports_bonus(user_id: int, context):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT aisports_bonus_received FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            if not row or row[0] == 0:
                asyncio.create_task(give_aisports_bonus(user_id, context.bot))

# ========== ADMIN ==========
async def is_admin(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone() is not None

async def add_admin(user_id: int, added_by: int) -> bool:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO admins (user_id, added_by) VALUES (?, ?)", (user_id, added_by))
            await db.commit()
            return True
    except:
        return False

async def remove_admin(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        await db.commit()
        return True

async def get_all_admins():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, added_by, added_at FROM admins ORDER BY added_at") as cur:
            return await cur.fetchall()

# ========== ANALYSIS (TAHLIL VA URL) ==========
async def update_analysis_text(match_id: int, analysis: str, added_by: int):
    """Faqat tahlil matnini yangilaydi, URL ga tegmaydi."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO match_analyses (match_id, analysis, added_by)
            VALUES (?, ?, ?)
            ON CONFLICT(match_id) DO UPDATE SET
                analysis = excluded.analysis,
                added_by = excluded.added_by,
                added_at = CURRENT_TIMESTAMP
        """, (match_id, analysis, added_by))
        await db.commit()

async def update_analysis_url(match_id: int, url: str, added_by: int):
    """Faqat URL ni yangilaydi, tahlil matniga tegmaydi.
       Agar match_id mavjud bo'lmasa, placeholder tahlil matni bilan qator yaratadi."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT analysis FROM match_analyses WHERE match_id = ?", (match_id,)) as cur:
            row = await cur.fetchone()
        if row:
            await db.execute("""
                UPDATE match_analyses 
                SET analysis_url = ?, added_by = ?, added_at = CURRENT_TIMESTAMP
                WHERE match_id = ?
            """, (url, added_by, match_id))
        else:
            await db.execute("""
                INSERT INTO match_analyses (match_id, analysis, analysis_url, added_by)
                VALUES (?, ?, ?, ?)
            """, (match_id, "📝 Tahlil kutilmoqda", url, added_by))
        await db.commit()

async def add_full_analysis(match_id: int, analysis: str, url: str, added_by: int):
    """Bir vaqtda tahlil matni va URL ni qo'shadi/yangilaydi."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO match_analyses (match_id, analysis, analysis_url, added_by)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(match_id) DO UPDATE SET
                analysis = excluded.analysis,
                analysis_url = excluded.analysis_url,
                added_by = excluded.added_by,
                added_at = CURRENT_TIMESTAMP
        """, (match_id, analysis, url, added_by))
        await db.commit()

async def get_analysis(match_id: int):
    """Tahlil matni, URL va qo'shilgan sanani qaytaradi."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT analysis, analysis_url, added_at FROM match_analyses WHERE match_id = ?", (match_id,)) as cur:
            return await cur.fetchone()

# ========== SUBSCRIPTIONS ==========
async def subscribe_user(user_id: int, match_id: int, match_time: str, home: str, away: str, league: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""INSERT OR REPLACE INTO subscriptions 
            (user_id, match_id, match_time, home_team, away_team, league_code, notified_1h, notified_15m, notified_lineups)
            VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0)""", (user_id, match_id, match_time, home, away, league))
        await db.commit()

async def unsubscribe_user(user_id: int, match_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM subscriptions WHERE user_id = ? AND match_id = ?", (user_id, match_id))
        await db.commit()

async def get_all_subscriptions():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""SELECT user_id, match_id, match_time, home_team, away_team, league_code,
            notified_1h, notified_15m, notified_lineups FROM subscriptions""") as cur:
            return await cur.fetchall()

async def update_notification_flags(user_id: int, match_id: int, **kwargs):
    async with aiosqlite.connect(DB_PATH) as db:
        updates = []
        params = []
        if kwargs.get('one_hour'):
            updates.append("notified_1h = 1")
        if kwargs.get('fifteen_min'):
            updates.append("notified_15m = 1")
        if kwargs.get('lineups'):
            updates.append("notified_lineups = 1")
        if not updates: return
        query = f"UPDATE subscriptions SET {', '.join(updates)} WHERE user_id = ? AND match_id = ?"
        params.extend([user_id, match_id])
        await db.execute(query, params)
        await db.commit()

async def get_subscribers_for_match(match_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM subscriptions WHERE match_id = ?", (match_id,)) as cur:
            rows = await cur.fetchall()
            return [r[0] for r in rows]

# ========== MATCH DATA FUNCTIONS ==========
async def fetch_matches_by_league(league_code: str):
    today = datetime.now().strftime("%Y-%m-%d")
    end_date = (datetime.now() + timedelta(days=DAYS_AHEAD)).strftime("%Y-%m-%d")
    url = f"{FOOTBALL_DATA_URL}/matches"
    params = {"competitions": league_code, "dateFrom": today, "dateTo": end_date, "status": "SCHEDULED,LIVE,IN_PLAY,PAUSED,FINISHED"}
    res = await rate_limited_api_call(url, HEADERS, params)
    if "success" in res:
        return {"success": res["success"].get("matches", [])}
    return res

async def fetch_match_lineups(match_id: int):
    match = await get_cached_match(match_id)
    if not match:
        return None
    home = match.get("homeTeam", {})
    away = match.get("awayTeam", {})
    return {
        "home_team": home.get("name", "Noma'lum"),
        "away_team": away.get("name", "Noma'lum"),
        "home_lineup": home.get("lineup", []),
        "away_lineup": away.get("lineup", []),
        "home_coach": home.get("coach", {}).get("name") if home.get("coach") else None,
        "away_coach": away.get("coach", {}).get("name") if away.get("coach") else None,
        "home_formation": home.get("formation"),
        "away_formation": away.get("formation"),
        "venue": match.get("venue"),
        "attendance": match.get("attendance")
    }

def format_lineups(data):
    if not data or (not data['home_lineup'] and not data['away_lineup']):
        return "📋 Tarkiblar hali e'lon qilinmagan."
    msg = f"⚽ **{data['home_team']} vs {data['away_team']}**\n\n"
    if data['venue']: msg += f"🏟️ Stadion: {data['venue']}\n"
    if data['attendance']: msg += f"👥 Tomoshabin: {data['attendance']}\n"
    msg += f"\n🏠 **{data['home_team']}**"
    if data['home_formation']: msg += f" ({data['home_formation']})"
    if data['home_coach']: msg += f" – Murabbiy: {data['home_coach']}"
    msg += "\n" + "━" * 30 + "\n"
    if data['home_lineup']:
        for p in data['home_lineup'][:11]:
            pos = p.get('position', '')
            icon = "🥅" if "Goalkeeper" in pos else "🛡️" if "Defender" in pos else "⚡" if "Midfielder" in pos else "🎯"
            shirt = p.get('shirtNumber', '')
            name = p.get('name', "Noma'lum")
            msg += f"{icon} {shirt} – {name} ({pos})\n"
    else:
        msg += "❌ Tarkib e'lon qilinmagan\n"
    msg += f"\n🛣️ **{data['away_team']}**"
    if data['away_formation']: msg += f" ({data['away_formation']})"
    if data['away_coach']: msg += f" – Murabbiy: {data['away_coach']}"
    msg += "\n" + "━" * 30 + "\n"
    if data['away_lineup']:
        for p in data['away_lineup'][:11]:
            pos = p.get('position', '')
            icon = "🥅" if "Goalkeeper" in pos else "🛡️" if "Defender" in pos else "⚡" if "Midfielder" in pos else "🎯"
            shirt = p.get('shirtNumber', '')
            name = p.get('name', "Noma'lum")
            msg += f"{icon} {shirt} – {name} ({pos})\n"
    else:
        msg += "❌ Tarkib e'lon qilinmagan\n"
    return msg

def generate_match_links(mid, home, away, league):
    links = []
    links.append(("📺 ESPN", f"https://www.espn.com/soccer/match/_/gameId/{mid}"))
    if league == "PL":
        links.append(("📰 BBC Sport", f"https://www.bbc.com/sport/football/{mid}"))
    links.append(("⚡ Sky Sports", f"https://www.skysports.com/football/{home.lower().replace(' ', '-')}-vs-{away.lower().replace(' ', '-')}/{mid}"))
    if league == "PD":
        links.append(("📘 MARCA", f"https://www.marca.com/futbol/primera-division/{mid}.html"))
        links.append(("📙 AS", f"https://as.com/futbol/primera/{mid}.html"))
    elif league == "SA":
        links.append(("📗 La Gazzetta", f"https://www.gazzetta.it/calcio/serie-a/match-{mid}.shtml"))
        links.append(("📕 Corriere", f"https://www.corriere.it/calcio/serie-a/{mid}.shtml"))
    elif league == "BL1":
        links.append(("📘 Kicker", f"https://www.kicker.de/{mid}/aufstellung"))
        links.append(("📙 Bild", f"https://www.bild.de/sport/fussball/bundesliga/{mid}.html"))
    elif league == "FL1":
        links.append(("📗 L'Equipe", f"https://www.lequipe.fr/Football/match/{mid}"))
        links.append(("📕 RMC Sport", f"https://rmcsport.bfmtv.com/football/match-{mid}.html"))
    links.append(("⚽ FlashScore", f"https://www.flashscore.com/match/{mid}/#/lineups"))
    links.append(("📊 SofaScore", f"https://www.sofascore.com/football/match/{mid}"))
    return links

def format_links_message(links):
    msg = "🔗 **Ishonchli saytlarda tarkiblarni ko‘ring:**\n\n"
    for name, url in links[:5]:
        msg += f"• [{name}]({url})\n"
    return msg

# ========== INLINE KEYBOARDS ==========
def money_row():
    return [InlineKeyboardButton("💰 Pul ishlash", callback_data="money_info"),
            InlineKeyboardButton("💳 Balans", callback_data="balance_info"),
            InlineKeyboardButton("💸 Pul yechish", callback_data="withdraw_info")]

def get_leagues_keyboard():
    kb = []
    for code, data in TOP_LEAGUES.items():
        kb.append([InlineKeyboardButton(data["name"], callback_data=f"league_{code}")])
    kb.append(money_row())
    return InlineKeyboardMarkup(kb)

def build_matches_keyboard(matches):
    kb = []
    for m in matches[:10]:
        date_obj = datetime.strptime(m["utcDate"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=5)
        date_str = date_obj.strftime("%d.%m %H:%M")
        kb.append([InlineKeyboardButton(f"{m['homeTeam']['name']} – {m['awayTeam']['name']} ({date_str})", callback_data=f"match_{m['id']}")])
    kb.append([InlineKeyboardButton("🔙 Back to Leagues", callback_data="leagues")])
    kb.append(money_row())
    return InlineKeyboardMarkup(kb)

def build_match_detail_keyboard(mid, is_subscribed=False, lineups_available=False, analysis_url=None):
    kb = []
    if is_subscribed:
        kb.append([InlineKeyboardButton("🔕 Kuzatishni bekor qilish", callback_data=f"unsubscribe_{mid}")])
    else:
        kb.append([InlineKeyboardButton("🔔 Kuzatish", callback_data=f"subscribe_{mid}")])
    if analysis_url:
        kb.append([InlineKeyboardButton("🔗 To‘liq tahlil", url=analysis_url)])
    kb.append([InlineKeyboardButton("📰 Futbol yangiliklari", url="https://t.me/ai_futinside"),
               InlineKeyboardButton("📊 Chuqur tahlil", url="https://futbolinside.netlify.app/"),
               InlineKeyboardButton("🎲 Stavka qilish", url="https://superlative-twilight-47ef34.netlify.app/")])
    if lineups_available:
        kb.append([InlineKeyboardButton("📋 Tarkiblarni ko‘rish", callback_data=f"lineups_{mid}")])
    kb.append([InlineKeyboardButton("🔙 Back to Leagues", callback_data="leagues")])
    kb.append(money_row())
    return InlineKeyboardMarkup(kb)

# ========== HANDLERS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    args = context.args
    ref = None
    if args and args[0].startswith("ref_"):
        try: ref = int(args[0].replace("ref_", ""))
        except: pass
        if ref == u.id: ref = None
    await get_or_create_user(u.id, ref, context.bot, u.first_name)
    await schedule_aisports_bonus(u.id, context)
    bot_username = (await context.bot.get_me()).username
    ref_link = await get_referral_link(u.id, bot_username)
    text = (f"👋 Assalomu alaykum, {u.first_name}!\n\n⚽ Ushbu bot orqali top 5 chempionat oʻyinlarini kuzatishingiz, "
            f"tahlillarni olishingiz va oʻyinlar haqida eslatmalarni sozlashingiz mumkin.\n\n"
            f"💰 **Pul ishlash imkoniyati**:\nDoʻstlaringizni taklif qiling va har bir taklif uchun **{REFERRAL_BONUS:,} soʻm** oling!\n"
            f"Sizning referal havolangiz:\n`{ref_link}`\n\n"
            f"💸 Minimal pul yechish: **{MIN_WITHDRAW:,} soʻm**, kuniga **1 marta**.\n\n"
            f"🎁 **Aisports maxsus sovgʻasi**: 30 000 soʻm bonus puli 1-2 daqiqadan soʻng hisobingizga qoʻshiladi!\n\n"
            f"Quyida ligalardan birini tanlang:")
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_leagues_keyboard())

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    uid = update.effective_user.id

    # ---------- PUL ISHLASH INFO + SHARE ----------
    if data == "money_info":
        bot_username = (await context.bot.get_me()).username
        ref_link = await get_referral_link(uid, bot_username)
        stats = await get_referral_stats(uid)
        bal = await get_user_balance(uid)
        text = (f"💰 **Pul ishlash tizimi**\n\n• Har bir doʻstingizni taklif qilish uchun: **+{REFERRAL_BONUS:,} soʻm**\n"
                f"• Minimal pul yechish: **{MIN_WITHDRAW:,} soʻm**\n• Kuniga **1 marta** pul yechish mumkin.\n\n"
                f"📊 **Sizning statistika:**\n• Balans: **{bal:,} soʻm**\n• Taklif qilinganlar: **{stats['count']} ta**\n"
                f"• Bugun taklif qilingan: **{stats['today_count']} ta**\n• Jami bonus: **{stats['total_bonus']:,} soʻm**\n\n"
                f"🔗 **Sizning referal havolangiz:**\n`{ref_link}`\n\n⚠️ Doʻstingiz botga start bosganida bonus avtomatik hisoblanadi.")
        share_text = (f"🤖 Futbol tahlillari va pul ishlash botiga taklif!\n\n"
                      f"Bot orqali top-5 chempionat oʻyinlarini kuzating, tahlillarni oling va doʻstlaringizni taklif qilib pul ishlang.\n\n"
                      f"🎁 Har bir taklif uchun +{REFERRAL_BONUS:,} soʻm bonus!\n👇 Quyidagi havola orqali botga oʻting:\n{ref_link}")
        share_url = f"https://t.me/share/url?url={quote(ref_link)}&text={quote(share_text)}"
        kb = [[InlineKeyboardButton("📤 Do'stlarga yuborish", url=share_url)],
              [InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_to_start")],
              money_row()]
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        return

    # ---------- BALANS ----------
    if data == "balance_info":
        bal = await get_user_balance(uid)
        stats = await get_referral_stats(uid)
        text = (f"💳 **Sizning balansingiz**\n\n💰 Balans: **{bal:,} soʻm**\n👥 Referallar: **{stats['count']} ta**\n"
                f"🎁 Bonus: **{stats['total_bonus']:,} soʻm**\n\n💸 Pul yechish uchun minimal miqdor: **{MIN_WITHDRAW:,} soʻm**\n📅 Kuniga **1 marta** yechish mumkin.")
        kb = [[InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_to_start")], money_row()]
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        return

    # ---------- PUL YECHISH ----------
    if data == "withdraw_info":
        bal = await get_user_balance(uid)
        if bal < MIN_WITHDRAW:
            text = f"❌ Sizda yetarli mablagʻ yoʻq.\nBalans: **{bal:,} soʻm**\nMinimal yechish: **{MIN_WITHDRAW:,} soʻm**\n\nDoʻstlaringizni taklif qilib pul ishlang!"
            kb = [[InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_to_start")], money_row()]
            await q.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            return
        can, msg = await can_withdraw(uid)
        if not can:
            kb = [[InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_to_start")], money_row()]
            await q.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            return
        success = await register_withdraw(uid, MIN_WITHDRAW)
        if success:
            kb = [[InlineKeyboardButton("💸 Pul yechish (test)", url="https://futbolinsidepulyechish.netlify.app/")],
                  [InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_to_start")], money_row()]
            await q.message.reply_text(
                f"✅ **Pul yechish soʻrovingiz qabul qilindi!**\n\nYechilgan miqdor: **{MIN_WITHDRAW:,} soʻm**\nQolgan balans: **{bal - MIN_WITHDRAW:,} soʻm**\n\n⚠️ Bu test rejimi. Pul yechish uchun quyidagi havolaga oʻting:",
                parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        else:
            kb = [[InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_to_start")], money_row()]
            await q.message.reply_text("❌ Xatolik yuz berdi. Qayta urinib koʻring.", reply_markup=InlineKeyboardMarkup(kb))
        return

    # ---------- BOSH MENYU ----------
    if data == "back_to_start":
        u = update.effective_user
        await get_or_create_user(u.id, None)
        bot_username = (await context.bot.get_me()).username
        ref_link = await get_referral_link(u.id, bot_username)
        text = (f"👋 Assalomu alaykum, {u.first_name}!\n\n⚽ Ushbu bot orqali top 5 chempionat oʻyinlarini kuzatishingiz, "
                f"tahlillarni olishingiz va oʻyinlar haqida eslatmalarni sozlashingiz mumkin.\n\n"
                f"💰 **Pul ishlash imkoniyati**:\nDoʻstlaringizni taklif qiling va har bir taklif uchun **{REFERRAL_BONUS:,} soʻm** oling!\n"
                f"Sizning referal havolangiz:\n`{ref_link}`\n\n"
                f"💸 Minimal pul yechish: **{MIN_WITHDRAW:,} soʻm**, kuniga **1 marta**.\n\nQuyida ligalardan birini tanlang:")
        await q.message.reply_text(text, parse_mode="Markdown", reply_markup=get_leagues_keyboard())
        return

    # ---------- FUTBOL ----------
    if data == "leagues":
        await q.edit_message_text("sport uchun eng yuqori sifatdagi taxlilarni olish uchun Quyidagi chempionatlardan birini tanlang:",
                                  reply_markup=get_leagues_keyboard())
        return

    if data.startswith("league_"):
        code = data.split("_")[1]
        info = TOP_LEAGUES.get(code)
        if not info:
            await q.edit_message_text("❌ Notoʻgʻri tanlov.")
            return
        await q.edit_message_text(f"⏳ {info['name']} – oʻyinlar yuklanmoqda...")
        res = await fetch_matches_by_league(code)
        if "error" in res:
            await q.edit_message_text(res["error"], reply_markup=get_leagues_keyboard())
            return
        matches = res["success"]
        if not matches:
            await q.edit_message_text(f"⚽ {info['name']}\n{DAYS_AHEAD} kun ichida oʻyinlar yoʻq.", reply_markup=get_leagues_keyboard())
            return
        await q.edit_message_text(f"🏆 **{info['name']}** – {DAYS_AHEAD} kun ichidagi oʻyinlar:\n\nOʻyin ustiga bosing, tahlil va kuzatish imkoniyati.",
                                  parse_mode="Markdown", reply_markup=build_matches_keyboard(matches))
        return

    if data.startswith("match_"):
        mid = int(data.split("_")[1])
        analysis_row = await get_analysis(mid)
        match = await get_cached_match(mid)
        league = "PL"
        home = away = "Noma'lum"
        if match:
            league = match.get("competition", {}).get("code", "PL")
            home = match.get("homeTeam", {}).get("name", "Noma'lum")
            away = match.get("awayTeam", {}).get("name", "Noma'lum")
        analysis_url = None
        if analysis_row:
            text, analysis_url, added_at = analysis_row
            date_str = datetime.strptime(added_at, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y %H:%M")
            safe_text = escape_markdown(text, version=2)
            msg = f"⚽ **Oʻyin tahlili**\n\n🆔 Match ID: `{mid}`\n📝 **Tahlil:**\n{safe_text}\n\n🕐 Qoʻshilgan: {date_str}"
        else:
            msg = f"⚽ **Oʻyin tahlili**\n\n🆔 Match ID: `{mid}`\n📊 Hozircha tahlil mavjud emas."
            if await is_admin(uid):
                msg += f"\n\n💡 Admin: `/addanalysis {mid} <tahlil>`"
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT 1 FROM subscriptions WHERE user_id = ? AND match_id = ?", (uid, mid)) as cur:
                subscribed = await cur.fetchone() is not None
        lineups = await fetch_match_lineups(mid)
        lineups_avail = lineups and (lineups['home_lineup'] or lineups['away_lineup'])
        kb = build_match_detail_keyboard(mid, subscribed, lineups_avail, analysis_url)

        if len(msg) > 4096:
            await q.edit_message_text(msg[:4090] + "...", parse_mode="Markdown", reply_markup=kb)
            await context.bot.send_message(uid, f"📎 **Tahlilning davomi:**\n\n{msg[4090:]}", parse_mode="Markdown")
        else:
            await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=kb)
        return

    if data.startswith("lineups_"):
        mid = int(data.split("_")[1])
        await q.edit_message_text("⏳ Tarkiblar yuklanmoqda...")
        lineups = await fetch_match_lineups(mid)
        if lineups and (lineups['home_lineup'] or lineups['away_lineup']):
            msg = format_lineups(lineups)
        else:
            msg = "❌ Bu oʻyin uchun tarkiblar hali eʼlon qilinmagan."
        match = await get_cached_match(mid)
        league = "PL"
        home = away = "Noma'lum"
        if match:
            league = match.get("competition", {}).get("code", "PL")
            home = match.get("homeTeam", {}).get("name", "Noma'lum")
            away = match.get("awayTeam", {}).get("name", "Noma'lum")
        links = generate_match_links(mid, home, away, league)
        msg += "\n\n" + format_links_message(links)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Oʻyinga qaytish", callback_data=f"match_{mid}")],
            [InlineKeyboardButton("🔙 Back to Leagues", callback_data="leagues")],
            money_row()])
        await q.edit_message_text(msg, parse_mode="Markdown", reply_markup=kb)
        return

    if data.startswith("subscribe_"):
        mid = int(data.split("_")[1])
        match = await get_cached_match(mid)
        if not match:
            await q.answer("❌ Match ma'lumotlarini olishda xatolik", show_alert=True)
            return
        home = match["homeTeam"]["name"]
        away = match["awayTeam"]["name"]
        t = match["utcDate"]
        league = match.get("competition", {}).get("code", "PL")
        await subscribe_user(uid, mid, t, home, away, league)
        analysis_row = await get_analysis(mid)
        analysis_url = analysis_row[1] if analysis_row else None
        new_kb = build_match_detail_keyboard(mid, is_subscribed=True, analysis_url=analysis_url)
        await q.edit_message_reply_markup(reply_markup=new_kb)
        await q.answer("✅ Kuzatish boshlandi!", show_alert=False)
        return

    if data.startswith("unsubscribe_"):
        mid = int(data.split("_")[1])
        await unsubscribe_user(uid, mid)
        analysis_row = await get_analysis(mid)
        analysis_url = analysis_row[1] if analysis_row else None
        new_kb = build_match_detail_keyboard(mid, is_subscribed=False, analysis_url=analysis_url)
        await q.edit_message_reply_markup(reply_markup=new_kb)
        await q.answer("❌ Kuzatish bekor qilindi", show_alert=False)
        return

# ========== NOTIFICATION SCHEDULER ==========
async def notification_scheduler(app: Application):
    while True:
        try:
            subs = await get_all_subscriptions()
            groups = {}
            for s in subs:
                uid, mid, tstr, home, away, league, n1, n15, nl = s
                if mid not in groups:
                    groups[mid] = {"time": datetime.strptime(tstr, "%Y-%m-%dT%H:%M:%SZ"), "home": home, "away": away, "league": league,
                                   "users": [], "n1_flag": False, "n15_flag": False, "nl_flag": False}
                groups[mid]["users"].append({"id": uid, "n1": n1, "n15": n15, "nl": nl})
            for mid, g in groups.items():
                delta = (g["time"] - datetime.utcnow()).total_seconds() / 60
                if not g["n1_flag"] and any(not u["n1"] for u in g["users"]):
                    if 55 <= delta <= 65:
                        for u in g["users"]:
                            if not u["n1"]:
                                try:
                                    await app.bot.send_message(u["id"],
                                        f"⏰ **1 soat qoldi!**\n\n{g['home']} – {g['away']}\n🕒 {g['time'].strftime('%d.%m.%Y %H:%M')} UTC+0\n\n📋 Tarkiblar eʼlon qilinishi kutilmoqda.",
                                        parse_mode="Markdown")
                                    await update_notification_flags(u["id"], mid, one_hour=True)
                                except Exception as e:
                                    logger.error(f"1h notification error: {e}")
                        g["n1_flag"] = True
                        if not g["nl_flag"] and any(not u["nl"] for u in g["users"]):
                            lu = await fetch_match_lineups(mid)
                            if lu and (lu['home_lineup'] or lu['away_lineup']):
                                lineup_msg = format_lineups(lu)
                                links = generate_match_links(mid, g['home'], g['away'], g['league'])
                                links_msg = format_links_message(links)
                                for u in g["users"]:
                                    if not u["nl"]:
                                        try:
                                            await app.bot.send_message(u["id"], lineup_msg, parse_mode="Markdown")
                                            await app.bot.send_message(u["id"], links_msg, parse_mode="Markdown", disable_web_page_preview=True)
                                            await update_notification_flags(u["id"], mid, lineups=True)
                                        except Exception as e:
                                            logger.error(f"Lineups notification error: {e}")
                            else:
                                links = generate_match_links(mid, g['home'], g['away'], g['league'])
                                msg = f"📋 **{g['home']} – {g['away']}**\n\n❌ Tarkiblar API orqali e'lon qilinmagan.\n🔗 Quyidagi ishonchli saytlarda tarkiblarni ko‘ring:\n\n"
                                for name, url in links[:4]:
                                    msg += f"• [{name}]({url})\n"
                                for u in g["users"]:
                                    if not u["nl"]:
                                        try:
                                            await app.bot.send_message(u["id"], msg, parse_mode="Markdown", disable_web_page_preview=True)
                                            await update_notification_flags(u["id"], mid, lineups=True)
                                        except Exception as e:
                                            logger.error(f"Lineups notification error: {e}")
                            g["nl_flag"] = True
                if not g["n15_flag"] and any(not u["n15"] for u in g["users"]):
                    if 10 <= delta <= 20:
                        links = generate_match_links(mid, g['home'], g['away'], g['league'])
                        msg = f"⏳ **15 daqiqa qoldi!**\n\n{g['home']} – {g['away']}\n🕒 {g['time'].strftime('%d.%m.%Y %H:%M')} UTC+0\n\n🔗 Jonli tarkiblar va statistika:\n\n"
                        for name, url in links[:5]:
                            msg += f"• [{name}]({url})\n"
                        for u in g["users"]:
                            if not u["n15"]:
                                try:
                                    await app.bot.send_message(u["id"], msg, parse_mode="Markdown", disable_web_page_preview=True)
                                    await update_notification_flags(u["id"], mid, fifteen_min=True)
                                except Exception as e:
                                    logger.error(f"15m notification error: {e}")
                        g["n15_flag"] = True
        except Exception as e:
            logger.exception(f"Scheduler xatosi: {e}")
        await asyncio.sleep(60)

# ========== ADMIN BUYRUQLARI ==========
async def add_analysis_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Faqat tahlil matnini qo'shadi/yangilaydi."""
    u = update.effective_user
    if not await is_admin(u.id):
        await update.message.reply_text("❌ Siz admin emassiz.")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ Ishlatish: `/addanalysis <match_id> <tahlil matni>`\n"
            "Misol: `/addanalysis 123456 Arsenal favorit!`",
            parse_mode="Markdown"
        )
        return

    try:
        match_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Match ID raqam boʻlishi kerak.")
        return

    text = ' '.join(context.args[1:])
    if not text:
        await update.message.reply_text("❌ Tahlil matni boʻsh boʻlishi mumkin emas.")
        return

    await update_analysis_text(match_id, text, u.id)
    await update.message.reply_text(f"✅ Tahlil matni qoʻshildi (Match ID: {match_id}).")

    subs = await get_subscribers_for_match(match_id)
    if subs:
        sent = 0
        safe_text = escape_markdown(text, version=2)
        buttons = [[InlineKeyboardButton("📋 Tahlilni ko‘rish", callback_data=f"match_{match_id}")]]
        keyboard = InlineKeyboardMarkup(buttons)
        for sid in subs:
            try:
                await context.bot.send_message(
                    sid,
                    f"📝 **Oʻyin tahlili yangilandi!**\n\n🆔 Match ID: `{match_id}`\n📊 **Yangi tahlil:**\n{safe_text}",
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
                sent += 1
            except Exception as e:
                logger.error(f"Tahlil bildirishnomasi xatosi (user {sid}): {e}")
        await update.message.reply_text(f"📢 {sent} ta obunachiga bildirishnoma yuborildi.")

async def add_url_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Faqat to'liq tahlil URL ini qo'shadi/yangilaydi."""
    u = update.effective_user
    if not await is_admin(u.id):
        await update.message.reply_text("❌ Siz admin emassiz.")
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "❌ Ishlatish: `/addurl <match_id> <havola>`\n"
            "Misol: `/addurl 123456 https://t.me/ai_futinside/29`",
            parse_mode="Markdown"
        )
        return

    try:
        match_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Match ID raqam boʻlishi kerak.")
        return

    url = context.args[1].strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        await update.message.reply_text("❌ Havola `http://` yoki `https://` bilan boshlanishi kerak.", parse_mode="Markdown")
        return

    await update_analysis_url(match_id, url, u.id)
    await update.message.reply_text(f"✅ Toʻliq tahlil havolasi qoʻshildi (Match ID: {match_id}).\n🔗 {url}")

    subs = await get_subscribers_for_match(match_id)
    if subs:
        sent = 0
        analysis_row = await get_analysis(match_id)
        analysis_text = analysis_row[0] if analysis_row else "Tahlil kutilmoqda"
        safe_text = escape_markdown(analysis_text, version=2)
        buttons = [
            [InlineKeyboardButton("📋 Tahlilni ko‘rish", callback_data=f"match_{match_id}")],
            [InlineKeyboardButton("🔗 To‘liq tahlil", url=url)]
        ]
        keyboard = InlineKeyboardMarkup(buttons)
        for sid in subs:
            try:
                await context.bot.send_message(
                    sid,
                    f"🔗 **Oʻyin uchun toʻliq tahlil havolasi qoʻshildi!**\n\n"
                    f"🆔 Match ID: `{match_id}`\n"
                    f"📊 **Tahlil:**\n{safe_text}",
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
                sent += 1
            except Exception as e:
                logger.error(f"URL bildirishnomasi xatosi (user {sid}): {e}")
        await update.message.reply_text(f"📢 {sent} ta obunachiga bildirishnoma yuborildi.")

async def add_full_analysis_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bir vaqtda tahlil matni va URL qo'shish."""
    u = update.effective_user
    if not await is_admin(u.id):
        await update.message.reply_text("❌ Siz admin emassiz.")
        return

    if len(context.args) < 3:
        await update.message.reply_text(
            "❌ Ishlatish: `/addfull <match_id> <tahlil matni> <havola>`\n"
            "Misol: `/addfull 123456 Arsenal favorit! https://t.me/ai_futinside/29`",
            parse_mode="Markdown"
        )
        return

    try:
        match_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Match ID raqam boʻlishi kerak.")
        return

    url = context.args[-1]
    if not (url.startswith("http://") or url.startswith("https://")):
        await update.message.reply_text("❌ Havola `http://` yoki `https://` bilan boshlanishi kerak.", parse_mode="Markdown")
        return

    text = ' '.join(context.args[1:-1])
    if not text:
        await update.message.reply_text("❌ Tahlil matni boʻsh boʻlishi mumkin emas.")
        return

    await add_full_analysis(match_id, text, url, u.id)
    await update.message.reply_text(
        f"✅ Tahlil va havola qoʻshildi (Match ID: {match_id}).\n🔗 {url}",
        parse_mode="Markdown"
    )

    subs = await get_subscribers_for_match(match_id)
    if subs:
        sent = 0
        safe_text = escape_markdown(text, version=2)
        buttons = [
            [InlineKeyboardButton("📋 Tahlilni ko‘rish", callback_data=f"match_{match_id}")],
            [InlineKeyboardButton("🔗 To‘liq tahlil", url=url)]
        ]
        keyboard = InlineKeyboardMarkup(buttons)
        for sid in subs:
            try:
                await context.bot.send_message(
                    sid,
                    f"📝 **Oʻyin tahlili va toʻliq tahlil havolasi qoʻshildi!**\n\n"
                    f"🆔 Match ID: `{match_id}`\n"
                    f"📊 **Tahlil:**\n{safe_text}",
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
                sent += 1
            except Exception as e:
                logger.error(f"Bildirishnoma xatosi (user {sid}): {e}")
        await update.message.reply_text(f"📢 {sent} ta obunachiga bildirishnoma yuborildi.")

async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not await is_admin(u.id): return await update.message.reply_text("❌ Siz admin emassiz.")
    if len(context.args) != 1: return await update.message.reply_text("❌ Ishlatish: `/addadmin 123456789`", parse_mode="Markdown")
    try: new = int(context.args[0])
    except: return await update.message.reply_text("❌ ID raqam boʻlishi kerak.")
    if await is_admin(new): return await update.message.reply_text("⚠️ Bu foydalanuvchi allaqachon admin.")
    if await add_admin(new, u.id):
        await update.message.reply_text(f"✅ Foydalanuvchi {new} admin qilindi.")
    else:
        await update.message.reply_text("❌ Xatolik yuz berdi.")

async def remove_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not await is_admin(u.id): return await update.message.reply_text("❌ Siz admin emassiz.")
    if len(context.args) != 1: return await update.message.reply_text("❌ Ishlatish: `/removeadmin 123456789`", parse_mode="Markdown")
    try: aid = int(context.args[0])
    except: return await update.message.reply_text("❌ ID raqam boʻlishi kerak.")
    if aid == 6935090105: return await update.message.reply_text("❌ Asosiy adminni o‘chirib bo‘lmaydi.")
    if not await is_admin(aid): return await update.message.reply_text("⚠️ Bu foydalanuvchi admin emas.")
    await remove_admin(aid)
    await update.message.reply_text(f"✅ Admin {aid} olib tashlandi.")

async def list_admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not await is_admin(u.id): return await update.message.reply_text("❌ Siz admin emassiz.")
    admins = await get_all_admins()
    if not admins: return await update.message.reply_text("📭 Adminlar ro'yxati bo'sh.")
    text = "👑 **Adminlar:**\n\n"
    for aid, added_by, at in admins:
        dt = datetime.strptime(at, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y")
        text += f"• `{aid}` – qo'shdi: `{added_by}`, {dt}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def admin_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not await is_admin(u.id): return await update.message.reply_text("❌ Siz admin emassiz.")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            users = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM referrals") as cur:
            refs = (await cur.fetchone())[0]
        async with db.execute("SELECT SUM(balance) FROM users") as cur:
            bal = (await cur.fetchone())[0] or 0
        async with db.execute("SELECT COUNT(*) FROM withdrawals WHERE status='completed'") as cur:
            wd_cnt = (await cur.fetchone())[0]
        async with db.execute("SELECT SUM(amount) FROM withdrawals WHERE status='completed'") as cur:
            wd_sum = (await cur.fetchone())[0] or 0
    text = f"📊 **Bot statistikasi**\n\n👥 Foydalanuvchilar: {users}\n🔗 Referallar: {refs}\n💰 Jami balans: {bal:,} soʻm\n💸 Yechimlar soni: {wd_cnt}\n💵 Jami yechilgan: {wd_sum:,} soʻm"
    await update.message.reply_text(text, parse_mode="Markdown")

async def test_api(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not FOOTBALL_DATA_KEY:
        await update.message.reply_text("❌ FOOTBALL_DATA_KEY topilmadi!")
    else:
        await update.message.reply_text("✅ API kaliti mavjud.")

async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Debug buyrug'i.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Quyidagi chempionatlardan birini tanlang:", reply_markup=get_leagues_keyboard())

# ========== WEB SERVER ==========
async def health_check(request):
    return web.Response(text="✅ Bot ishlamoqda (AddAnalysis + AddUrl + AddFull)")

async def run_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info(f"Web server port {port} da ishga tushdi")

# ========== MAIN ==========
async def run_bot():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN topilmadi!")
        return
    await init_db()
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("test", test_api))
    app.add_handler(CommandHandler("debug", debug))
    app.add_handler(CommandHandler("stats", admin_stats_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CommandHandler("addanalysis", add_analysis_command))
    app.add_handler(CommandHandler("addurl", add_url_command))
    app.add_handler(CommandHandler("addfull", add_full_analysis_command))
    app.add_handler(CommandHandler("addadmin", add_admin_command))
    app.add_handler(CommandHandler("removeadmin", remove_admin_command))
    app.add_handler(CommandHandler("listadmins", list_admins_command))
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    logger.info("🤖 Bot ishga tushdi! (AddAnalysis + AddUrl + AddFull)")
    asyncio.create_task(notification_scheduler(app))
    while True:
        await asyncio.sleep(3600)

async def main():
    await asyncio.gather(run_web_server(), run_bot())

if __name__ == "__main__":
    asyncio.run(main())
