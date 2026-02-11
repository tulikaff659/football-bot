import os
import asyncio
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Loglashni sozlash
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------- Futbol tahlillari (statik misol) ----------
def get_football_analysis():
    """Bugungi futbol tahlillarini qaytaradi (API kalitsiz, statik)."""
    today = datetime.now().strftime("%Y-%m-%d")
    analysis = f"""
⚽ **Futbol Tahlili – {today}**  

🏆 **Premyer Liga**  
Manchester City vs Liverpul  
- City gʻalabasi: 58%  
- Durang: 24%  
- Liverpul gʻalabasi: 18%  
🔑 Asosiy oʻyinchi: Erling Haaland (City)

🇪🇸 **La Liga**  
Barselona vs Real Madrid  
- Barselona gʻalabasi: 52%  
- Durang: 26%  
- Real Madrid gʻalabasi: 22%  
🔑 Asosiy oʻyinchi: Jude Bellingham (Real)

🇮🇹 **Seriya A**  
Yuventus vs Inter  
- Yuventus gʻalabasi: 45%  
- Durang: 30%  
- Inter gʻalabasi: 25%  
🔑 Asosiy oʻyinchi: Lautaro Martines (Inter)

📊 *Bashoratlar soʻnggi forma va tarixiy maʼlumotlarga asoslangan.*
    """
    return analysis

# ---------- Buyruq handlerlari ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Salomlashuv va futbol tahlilini yuborish."""
    user = update.effective_user
    welcome = f"👋 Assalomu alaykum, {user.first_name}!\n\n"
    analysis = get_football_analysis()
    await update.message.reply_text(welcome + analysis, parse_mode="Markdown")

async def analysis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Futbol tahlilini yuborish."""
    analysis = get_football_analysis()
    await update.message.reply_text(analysis, parse_mode="Markdown")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi yozgan har qanday matnga tahlil bilan javob berish."""
    await analysis(update, context)

# ---------- Asosiy funksiya ----------
def main():
    """Botni ishga tushirish."""
    # Bot tokenini muhit oʻzgaruvchisidan olish
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN muhit oʻzgaruvchisida topilmadi!")

    # Application yaratish
    application = Application.builder().token(token).build()

    # Handlerlarni qoʻshish
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("analysis", analysis))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    # Botni ishga tushirish (polling)
    logger.info("Bot ishga tushdi va polling qilmoqda...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
