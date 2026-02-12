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

    # 📢 Yangi foydalanuvchi – bot va ismni uzatamiz
    await get_or_create_user(
        user_id, 
        referrer_id, 
        bot=context.bot, 
        referred_user_name=user.first_name
    )
    
    # 🎁 Aisports bonusini rejalashtirish
    await schedule_aisports_bonus(user_id, context)
    
    # ... qolgan qismi o‘zgarmaydi
