import telebot
from telebot import types
import os
from datetime import datetime

# Bot token
BOT_TOKEN = os.environ.get('BOT_TOKEN', "8200926398:AAEeHOtOWRXxeBTRGGm14vUR1ymczX3ZoZY")
bot = telebot.TeleBot(BOT_TOKEN)

# Chempionatlar
LEAGUES = {
    "PL": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Angliya",
    "PD": "🇪🇸 Ispaniya",
    "FL1": "🇫🇷 Fransiya",
    "SA": "🇮🇹 Italiya",
    "BL1": "🇩🇪 Germaniya"
}

# O'yinlar (test ma'lumotlari)
MATCHES = {
    "PL": [
        {"home": "Manchester City", "away": "Arsenal", "time": "21:45", "prob": 67},
        {"home": "Liverpool", "away": "Chelsea", "time": "20:00", "prob": 58}
    ],
    "PD": [
        {"home": "Real Madrid", "away": "Barcelona", "time": "23:00", "prob": 55},
        {"home": "Atletico Madrid", "away": "Sevilla", "time": "21:15", "prob": 62}
    ],
    "FL1": [
        {"home": "PSG", "away": "Marseille", "time": "22:00", "prob": 71},
    ],
    "SA": [
        {"home": "Inter", "away": "Milan", "time": "21:45", "prob": 52},
        {"home": "Juventus", "away": "Napoli", "time": "20:00", "prob": 60}
    ],
    "BL1": [
        {"home": "Bayern", "away": "Dortmund", "time": "20:30", "prob": 65},
    ]
}

@bot.message_handler(commands=['start'])
def start(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    for code, name in LEAGUES.items():
        btn = types.InlineKeyboardButton(name, callback_data=f"league_{code}")
        markup.add(btn)
    
    text = """
⚽ *FUTBOL TAHLIL BOTI*

24 soat ichidagi o'yinlar:
🏴󠁧󠁢󠁥󠁮󠁧󠁿 Angliya
🇪🇸 Ispaniya
🇫🇷 Fransiya
🇮🇹 Italiya
🇩🇪 Germaniya

👇 Chempionatni tanlang:
    """
    
    bot.send_message(message.chat.id, text, parse_mode='Markdown', reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    if call.data.startswith("league_"):
        code = call.data.replace("league_", "")
        name = LEAGUES.get(code, "Noma'lum")
        matches = MATCHES.get(code, [])
        
        if not matches:
            bot.send_message(call.message.chat.id, f"❌ {name} da o'yin yo'q")
            bot.answer_callback_query(call.id)
            return
        
        text = f"🏆 *{name}*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        for match in matches:
            text += f"⚔️ *{match['home']}* vs *{match['away']}*\n"
            text += f"⏰ {match['time']}\n"
            text += f"📊 *Favorit:* {match['home']} ({match['prob']}%)\n"
            text += f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        bot.send_message(call.message.chat.id, text, parse_mode='Markdown')
        bot.answer_callback_query(call.id)

print("✅ Bot ishga tushdi!")
bot.infinity_polling()
