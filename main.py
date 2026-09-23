import asyncio
import logging
from datetime import datetime, timedelta
import sqlite3
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command

# ==================== CONFIGURATION ====================
BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"  # BotFather থেকে পাওয়া বট টোকেন
AFFILIATE_API_KEY = "YOUR_BROKER_API_KEY"  # ব্রোকার অ্যাফিলিয়েট এপিআই কি
VIP_GROUP_ID = -100123456789  # আপনার VIP গ্রুপের Chat ID
MIN_DEPOSIT = 10.0  # সর্বনিম্ন প্রয়োজনীয় ডিপোজিট ($10)
INACTIVE_DAYS_LIMIT = 15  # যত দিন নিষ্ক্রিয় থাকলে অটো-কিক হবে

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==================== DATABASE SETUP ====================
conn = sqlite3.connect('vip_bot_data.db')
cursor = conn.cursor()

# ইউজার টেবিল
cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        trader_id TEXT UNIQUE,
        join_date TIMESTAMP,
        last_active TIMESTAMP
    )
''')
conn.commit()

# ==================== HELPER FUNCTIONS ====================
async def check_broker_api(trader_id: str):
    """
    ব্রোকার এপিআই দিয়ে ইউজার আইডি ও ডিপোজিট ভেরিফাই করা।
    (আপনার নির্দিষ্ট ব্রোকারের API ডকুমেন্টেশন অনুযায়ী URL পরিবর্তন করবেন)
    """
    api_url = f"https://api.yourbroker.com/affiliate/check?key={AFFILIATE_API_KEY}&trader_id={trader_id}"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    is_referred = data.get("is_referred", False)
                    deposit_amount = float(data.get("deposit_amount", 0))
                    
                    if is_referred and deposit_amount >= MIN_DEPOSIT:
                        return True, "SUCCESS"
                    elif is_referred and deposit_amount < MIN_DEPOSIT:
                        return False, f"আপনার ডিপোজিট ${deposit_amount}। ন্যূনতম ${MIN_DEPOSIT} প্রয়োজন।"
                    else:
                        return False, "আপনি আমাদের রেফারেল লিংক দিয়ে অ্যাকাউন্ট খুলেননি।"
    except Exception as e:
        # API কাজ না করলে টেস্টের জন্য ডামি রিটার্ন রাখা যেতে পারে
        logging.error(f"Broker API Error: {e}")
        return False, "ব্রোকার সার্ভারের সাথে যোগাযোগ করা যাচ্ছে না। পরে চেষ্টা করুন।"
    
    return False, "ভেরিফিকেশন ব্যর্থ হয়েছে।"

# ==================== HANDLERS ====================

# ১. /start কম্যান্ড ইনবক্সে
@dp.message(Command("start"), F.chat.type == "private")
async def start_handler(message: types.Message):
    welcome_text = (
        "👋 **VIP ট্রেডিং গ্রুপে স্বাগতম!**\n\n"
        "ভিআইপি এক্সেস পেতে নিচের ধাপগুলো অনুসরণ করুন:\n"
        "1️⃣ আমাদের কাস্টম লিংক দিয়ে ব্রোকার অ্যাকাউন্ট তৈরি করুন।\n"
        "2️⃣ ন্যূনতম **$10** ডিপোজিট করুন।\n"
        "3️⃣ আপনার **Trader ID (UID)** এখানে ইনবক্সে পাঠিয়ে দিন।"
    )
    await message.answer(welcome_text, parse_mode="Markdown")

# ২. Trader ID গ্রহণের মাধ্যমে অটো-ভেরিফিকেশন
@dp.message(F.chat.type == "private", F.text.isdigit())
async def process_trader_id(message: types.Message):
    trader_id = message.text.strip()
    user_id = message.from_user.id
    
    await message.answer("🔄 আপনার Trader ID যাচাই করা হচ্ছে...")
    
    is_valid, reason = await check_broker_api(trader_id)
    
    if is_valid:
        now = datetime.now()
        cursor.execute('''
            INSERT INTO users (user_id, trader_id, join_date, last_active)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET trader_id=?, last_active=?
        ''', (user_id, trader_id, now, now, trader_id, now))
        conn.commit()
        
        # একবার ব্যবহারযোগ্য ইনভাইট লিংক তৈরি
        invite_link = await bot.create_chat_invite_link(
            chat_id=VIP_GROUP_ID,
            member_limit=1,
            expire_date=datetime.now() + timedelta(hours=1)
        )
        
        await message.answer(
            f"✅ **ভেরিফিকেশন সফল হয়েছে!**\n\n"
            f"নিচের লিংকে ক্লিক করে আমাদের VIP গ্রুপে জয়েন করুন (মেয়াদ ১ ঘণ্টা):\n"
            f"{invite_link.invite_link}",
            parse_mode="Markdown"
        )
    else:
        await message.answer(f"❌ **ভেরিফিকেশন ব্যর্থ হয়েছে!**\n\nকারণ: {reason}")

# ৩. গ্রুপে মেম্বারদের ফিডব্যাক/অ্যাক্টিভিটি ট্র্যাকিং
@dp.message(F.chat.id == VIP_GROUP_ID)
async def track_group_activity(message: types.Message):
    user_id = message.from_user.id
    now = datetime.now()
    
    # মেম্বারের মেসেজের পর তার last_active আপডেট করা
    cursor.execute('''
        UPDATE users SET last_active = ? WHERE user_id = ?
    ''', (now, user_id))
    conn.commit()

# ৪. গ্রুপের সিকিউরিটি: লিংক বা মিডিয়া ব্লক করা (সাধারণ মেম্বারদের জন্য)
@dp.message(F.chat.id == VIP_GROUP_ID, F.entities)
async def anti_spam_links(message: types.Message):
    # লিংক বা প্রমোশন দিলে ডিলিট করে দেওয়া
    for entity in message.entities:
        if entity.type in ["url", "text_link"]:
            try:
                await message.delete()
                warning = await message.answer(f"⚠️ @{message.from_user.username}, গ্রুপে কোনো লিংক বা প্রচার পোস্ট করা নিষিদ্ধ!")
                await asyncio.sleep(5)
                await warning.delete()
            except Exception as e:
                print(f"Error deleting link: {e}")

# ==================== AUTO-KICK TASK ====================
async def check_and_kick_inactive_users():
    """নিষ্ক্রিয় মেম্বারদের ট্র্যাক করে কিক করার ব্যাকগ্রাউন্ড প্রসেস"""
    while True:
        cutoff_date = datetime.now() - timedelta(days=INACTIVE_DAYS_LIMIT)
        
        cursor.execute('SELECT user_id FROM users WHERE last_active < ?', (cutoff_date,))
        inactive_users = cursor.fetchall()
        
        for (user_id,) in inactive_users:
            try:
                # কিক করে সাথে সাথে আনব্যান করা (যাতে পরে ভেরিফাই করে ঢুকতে পারে)
                await bot.ban_chat_member(chat_id=VIP_GROUP_ID, user_id=user_id)
                await bot.unban_chat_member(chat_id=VIP_GROUP_ID, user_id=user_id)
                
                # ইনবক্সে ওয়ার্নিং দেওয়া
                await bot.send_message(
                    chat_id=user_id,
                    text=f"❌ আপনি {INACTIVE_DAYS_LIMIT} দিন যাবত VIP গ্রুপে নিষ্ক্রিয় থাকায় আপনাকে রিমুভ করা হয়েছে।"
                )
                
                cursor.execute('DELETE FROM users WHERE user_id = ?', (user_id,))
                conn.commit()
            except Exception as e:
                logging.error(f"User {user_id} কিক করতে ব্যর্থ: {e}")
                
        # প্রতি ১২ ঘণ্টা পর পর অটো চেক চলবে
        await asyncio.sleep(43200)

# ==================== MAIN RUNNER ====================
async def main():
    logging.basicConfig(level=logging.INFO)
    asyncio.create_task(check_and_kick_inactive_users())
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
