import asyncio
import logging
from datetime import datetime, timedelta
import sqlite3
from fastapi import FastAPI, Request
import uvicorn
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command

# ==================== CONFIGURATION ====================
BOT_TOKEN = "8731292465:AAEKVlBpNsrSDEcHQhWK3JCel1tjidCLfho"
VIP_GROUP_ID = -1004407938236
PUBLIC_GROUP_ID = -1004414179767
ADMIN_ID = 7824116455

MIN_DEPOSIT = 10.0          # সর্বনিম্ন প্রয়োজনীয় ডিপোজিট ($10)
INACTIVE_DAYS_LIMIT = 15    # কত দিন নিষ্ক্রিয় থাকলে অটো-কিক হবে

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()

# ==================== DATABASE SETUP ====================
conn = sqlite3.connect('vip_bot_data.db', check_same_thread=False)
cursor = conn.cursor()

# Quotex থেকে অনুমোদিত ট্রেডারদের টেবিল
cursor.execute('''
    CREATE TABLE IF NOT EXISTS approved_traders (
        trader_id TEXT PRIMARY KEY,
        deposit_amount REAL,
        status TEXT
    )
''')

# টেলিগ্রাম ইউজার ট্র্যাকিং টেবিল
cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        trader_id TEXT,
        join_date TIMESTAMP,
        last_active TIMESTAMP
    )
''')
conn.commit()

# ==================== FASTAPI POSTBACK ENDPOINT ====================

@app.get("/postback")
@app.post("/postback")
async def receive_quotex_postback(request: Request):
    """
    Quotex থেকে পোস্টব্যাক ডাটা রিসিভ করার এন্ডপয়েন্ট।
    Quotex URL Format: https://your-app.onrender.com/postback?trader_id={trader_id}&deposit={sumdep}&status={status}
    """
    params = dict(request.query_params)
    
    trader_id = params.get("trader_id") or params.get("subid") or params.get("click_id")
    deposit = float(params.get("deposit") or params.get("sumdep") or 0)
    
    if trader_id and deposit >= MIN_DEPOSIT:
        cursor.execute('''
            INSERT INTO approved_traders (trader_id, deposit_amount, status)
            VALUES (?, ?, ?)
            ON CONFLICT(trader_id) DO UPDATE SET deposit_amount=?
        ''', (str(trader_id).strip(), deposit, 'APPROVED', deposit))
        conn.commit()
        
        logging.info(f"Quotex Postback Success: Trader ID {trader_id} deposited ${deposit}")
        return {"status": "success", "message": "Trader Approved"}
        
    return {"status": "ignored", "reason": "Insufficient deposit or missing trader_id"}

# ==================== TELEGRAM BOT HANDLERS ====================

# ১. /start কমান্ড
@dp.message(Command("start"), F.chat.type == "private")
async def start_handler(message: types.Message):
    welcome_text = (
        "👋 **VIP ট্রেডিং গ্রুপে স্বাগতম!**\n\n"
        "ভিআইপি এক্সেস পেতে নিচের ধাপগুলো অনুসরণ করুন:\n"
        "1️⃣ আমাদের রেফারেল লিংক দিয়ে Quotex অ্যাকাউন্ট খুলুন।\n"
        "2️⃣ সর্বনিন্ম **$10** ডিপোজিট সম্পন্ন করুন।\n"
        "3️⃣ আপনার **Trader ID (UID)** লিখে এখানে ইনবক্সে পাঠান।"
    )
    await message.answer(welcome_text, parse_mode="Markdown")

# ২. Trader ID গ্রহণ ও অটো ভেরিফিকেশন
@dp.message(F.chat.type == "private", F.text.isdigit())
async def process_trader_id(message: types.Message):
    trader_id = message.text.strip()
    user_id = message.from_user.id
    
    # পোস্টব্যাক দিয়ে অনুমোদিত তালিকায় আছে কি না চেক
    cursor.execute('SELECT deposit_amount FROM approved_traders WHERE trader_id = ?', (trader_id,))
    result = cursor.fetchone()
    
    if result:
        deposit_amount = result[0]
        now = datetime.now()
        
        cursor.execute('''
            INSERT INTO users (user_id, trader_id, join_date, last_active)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET last_active=?, trader_id=?
        ''', (user_id, trader_id, now, now, now, trader_id))
        conn.commit()
        
        # ১ ঘণ্টা মেয়াদের ১-টাইম VIP ইনভাইট লিংক
        invite_link = await bot.create_chat_invite_link(
            chat_id=VIP_GROUP_ID,
            member_limit=1,
            expire_date=datetime.now() + timedelta(hours=1)
        )
        
        await message.answer(
            f"✅ **ভেরিফিকেশন সফল হয়েছে!**\n\n"
            f"আপনার প্রাপ্ত ডিপোজিট: **${deposit_amount}**\n"
            f"নিচের লিংকে ক্লিক করে VIP গ্রুপে যোগ দিন (মেয়াদ ১ ঘণ্টা):\n{invite_link.invite_link}",
            parse_mode="Markdown"
        )
        
        # অ্যাডমিনকে নোটিফিকেশন পাঠানো
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🔔 **নতুন VIP মেম্বার ভেরিফাইড!**\nUser: @{message.from_user.username}\nID: `{user_id}`\nTrader ID: `{trader_id}`\nDeposit: ${deposit_amount}"
        )
    else:
        await message.answer(
            "❌ **Trader ID পাওয়া যায়নি বা ডিপোজিট কনফার্ম হয়নি!**\n\n"
            "অনুগ্রহ করে নিশ্চিত করুন:\n"
            "• আপনি আমাদের লিংক দিয়ে একাউন্ট খুলেছেন।\n"
            "• কমপক্ষে $10 ডিপোজিট করেছেন।\n\n"
            "ডিপোজিট করার ১-২ মিনিট পর আবার আপনার ID পাঠ জানান।"
        )

# ৩. VIP গ্রুপে ইউজার অ্যাক্টিভিটি ট্র্যাকিং
@dp.message(F.chat.id == VIP_GROUP_ID)
async def track_group_activity(message: types.Message):
    user_id = message.from_user.id
    now = datetime.now()
    cursor.execute('UPDATE users SET last_active = ? WHERE user_id = ?', (now, user_id))
    conn.commit()

# ৪. এন্টি-স্প্যাম লিংক ফিল্টার (গ্রুপে স্প্যামিং ডিলিট করা)
@dp.message(F.chat.id == VIP_GROUP_ID, F.entities)
async def anti_spam_links(message: types.Message):
    for entity in message.entities:
        if entity.type in ["url", "text_link"]:
            try:
                await message.delete()
                warning = await message.answer(f"⚠️ @{message.from_user.username}, গ্রুপে লিংক পোস্ট করা নিষিদ্ধ!")
                await asyncio.sleep(5)
                await warning.delete()
            except Exception as e:
                logging.error(f"Error deleting link: {e}")

# ==================== INACTIVE MEMBER KICK TASK ====================

async def check_and_kick_inactive_users():
    while True:
        cutoff_date = datetime.now() - timedelta(days=INACTIVE_DAYS_LIMIT)
        cursor.execute('SELECT user_id FROM users WHERE last_active < ?', (cutoff_date,))
        inactive_users = cursor.fetchall()
        
        for (user_id,) in inactive_users:
            try:
                await bot.ban_chat_member(chat_id=VIP_GROUP_ID, user_id=user_id)
                await bot.unban_chat_member(chat_id=VIP_GROUP_ID, user_id=user_id)
                
                await bot.send_message(
                    chat_id=user_id,
                    text=f"❌ আপনি {INACTIVE_DAYS_LIMIT} দিন ধরে VIP গ্রুপে নিষ্ক্রিয় থাকায় আপনাকে রিমুভ করা হয়েছে।"
                )
                cursor.execute('DELETE FROM users WHERE user_id = ?', (user_id,))
                conn.commit()
            except Exception as e:
                logging.error(f"Kick failed for user {user_id}: {e}")
                
        await asyncio.sleep(43200) # প্রতি ১২ ঘণ্টা পর পর অটো রান হবে

# ==================== STARTUP & RUNNER ====================

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(check_and_kick_inactive_users())
    asyncio.create_task(dp.start_polling(bot))

if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=8000)
