import telebot
from telebot import types
import os

# Bot token (Railway'da environment variable dan olinadi)
BOT_TOKEN = os.environ.get('BOT_TOKEN')
bot = telebot.TeleBot(BOT_TOKEN)

# Chempionatlar
LEAGUES = {
    "PL": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Angliya",
    "PD": "🇪🇸 Ispaniya",
    "FL1": "🇫🇷 Fransiya",
    "SA": "🇮🇹 Italiya",
    "BL1": "🇩🇪 Germaniya"
}

@bot.message_handler(commands=['start'])
def start(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    for code, name in LEAGUES.items():
        markup.add(types.InlineKeyboardButton(name, callback_data=f"league_{code}"))
    
    bot.send_message(
        message.chat.id,
        "⚽ *FUTBOL BOTI ISHGA TUSHDI!*\n\n24 soat ichidagi o'yinlar uchun chempionatni tanlang:",
        parse_mode='Markdown',
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    if call.data.startswith("league_"):
        code = call.data.replace("league_", "")
        name = LEAGUES.get(code, "Noma'lum")
        
        # Test o'yinlari
        matches = {
            "PL": "🏆 Manchester City vs Arsenal (21:45)\n📊 Favorit: Man City 67%\n\n🏆 Liverpool vs Chelsea (20:00)\n📊 Favorit: Liverpool 58%",
            "PD": "🏆 Real Madrid vs Barcelona (23:00)\n📊 Favorit: Real Madrid 55%",
            "FL1": "🏆 PSG vs Marseille (22:00)\n📊 Favorit: PSG 71%",
            "SA": "🏆 Inter vs Milan (21:45)\n📊 Favorit: Inter 52%",
            "BL1": "🏆 Bayern vs Dortmund (20:30)\n📊 Favorit: Bayern 65%"
        }
        
        text = f"🏆 *{name}*\n━━━━━━━━━━━━━━━\n\n{matches.get(code, '24 soat ichida o\'yin yo\'q')}"
        bot.send_message(call.message.chat.id, text, parse_mode='Markdown')
    
    bot.answer_callback_query(call.id)

print("✅ Bot ishga tushdi!")
bot.infinity_polling()
