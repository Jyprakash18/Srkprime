import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from aiohttp import web
from typing import Optional

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
PREMIUM_CHAT_ID = os.getenv("PREMIUM_CHAT_ID", "").strip()
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@admin")
DB_PATH = os.getenv("DB_PATH", "premium_bot.db")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing. Add it in .env or environment variables.")
if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS missing. Example: ADMIN_IDS=123456789")
if not PREMIUM_CHAT_ID:
    raise RuntimeError("PREMIUM_CHAT_ID missing. Example: PREMIUM_CHAT_ID=-1001234567890")

# Edit these plan prices as needed
PLANS = {
    "1m": {"name": "1 Month", "days": 30, "amount": 99},
    "3m": {"name": "3 Months", "days": 90, "amount": 249},
    "6m": {"name": "6 Months", "days": 180, "amount": 449},
    "1y": {"name": "1 Year", "days": 365, "amount": 799},
}

bot = Bot(BOT_TOKEN)
dp = Dispatcher()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def dt_to_str(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def str_to_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def plans_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for code, plan in PLANS.items():
        rows.append([InlineKeyboardButton(text=f"{plan['name']} - ₹{plan['amount']}", callback_data=f"plan:{code}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def approval_keyboard(payment_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Approve", callback_data=f"approve:{payment_id}:{user_id}"),
            InlineKeyboardButton(text="❌ Reject", callback_data=f"reject:{payment_id}:{user_id}"),
        ]
    ])


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            selected_plan TEXT,
            payment_status TEXT DEFAULT 'none',
            premium_start TEXT,
            premium_expiry TEXT,
            access_status TEXT DEFAULT 'none',
            created_at TEXT,
            updated_at TEXT
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            plan_code TEXT,
            amount INTEGER,
            screenshot_file_id TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            updated_at TEXT
        )
        """)
        await db.commit()


async def upsert_user_obj(user, selected_plan: Optional[str] = None, payment_status: Optional[str] = None) -> None:
    timestamp = dt_to_str(now_utc())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO users (user_id, username, first_name, selected_plan, payment_status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name,
            selected_plan=COALESCE(excluded.selected_plan, users.selected_plan),
            payment_status=COALESCE(excluded.payment_status, users.payment_status),
            updated_at=excluded.updated_at
        """, (user.id, user.username, user.first_name, selected_plan, payment_status, timestamp, timestamp))
        await db.commit()


async def upsert_user(message: Message, selected_plan: Optional[str] = None, payment_status: Optional[str] = None) -> None:
    await upsert_user_obj(message.from_user, selected_plan, payment_status)


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        return await cur.fetchone()


async def create_payment(user_id: int, plan_code: str, amount: int, file_id: str) -> int:
    timestamp = dt_to_str(now_utc())
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO payments (user_id, plan_code, amount, screenshot_file_id, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
        """, (user_id, plan_code, amount, file_id, timestamp, timestamp))
        await db.commit()
        return cur.lastrowid


async def activate_premium(user_id: int, plan_code: str) -> datetime:
    plan = PLANS[plan_code]
    start = now_utc()
    expiry = start + timedelta(days=plan["days"])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE users
        SET selected_plan=?, payment_status='approved', premium_start=?, premium_expiry=?, access_status='active', updated_at=?
        WHERE user_id=?
        """, (plan_code, dt_to_str(start), dt_to_str(expiry), dt_to_str(now_utc()), user_id))
        await db.commit()
    return expiry


async def remove_premium(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE users
        SET payment_status='removed', access_status='removed', updated_at=?
        WHERE user_id=?
        """, (dt_to_str(now_utc()), user_id))
        await db.commit()


async def set_payment_status(payment_id: int, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE payments SET status=?, updated_at=? WHERE id=?", (status, dt_to_str(now_utc()), payment_id))
        await db.commit()


@dp.message(Command("start"))
async def start_cmd(message: Message):
    await upsert_user(message)
    await message.answer(
        "👋 Welcome!\n\nPremium plan choose karo:",
        reply_markup=plans_keyboard()
    )


@dp.message(Command("renew"))
async def renew_cmd(message: Message):
    await upsert_user(message)
    await message.answer("Renewal ke liye plan choose karo:", reply_markup=plans_keyboard())


@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        f"Help / Support: {SUPPORT_USERNAME}\n\n"
        "Commands:\n/start - plans show\n/myplan - current plan\n/renew - renew premium"
    )


@dp.message(Command("myplan"))
async def myplan_cmd(message: Message):
    row = await get_user(message.from_user.id)
    if not row or row["access_status"] != "active":
        await message.answer("Aapka premium active nahi hai. /renew use karke plan choose karo.")
        return
    plan = PLANS.get(row["selected_plan"], {"name": row["selected_plan"]})
    expiry = str_to_dt(row["premium_expiry"])
    expiry_text = expiry.astimezone().strftime("%d-%m-%Y %I:%M %p") if expiry else "N/A"
    await message.answer(f"✅ Current plan: {plan['name']}\n⏳ Expiry: {expiry_text}")


@dp.callback_query(F.data.startswith("plan:"))
async def plan_selected(call: CallbackQuery):
    plan_code = call.data.split(":", 1)[1]
    plan = PLANS[plan_code]
    await upsert_user_obj(call.from_user, selected_plan=plan_code, payment_status="awaiting_screenshot")
    await call.message.answer(
        f"✅ Plan selected: {plan['name']}\n"
        f"💰 Amount: ₹{plan['amount']}\n\n"
        "Payment already filtered/received hone ke baad yahan payment screenshot upload karo.\n"
        "Sirf image/photo screenshot send karo."
    )
    await call.answer()


@dp.message(F.photo)
async def screenshot_received(message: Message):
    row = await get_user(message.from_user.id)
    if not row or not row["selected_plan"]:
        await message.answer("Pehle /start karke plan choose karo.")
        return

    plan_code = row["selected_plan"]
    plan = PLANS[plan_code]
    file_id = message.photo[-1].file_id
    payment_id = await create_payment(message.from_user.id, plan_code, plan["amount"], file_id)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET payment_status='pending', updated_at=? WHERE user_id=?", (dt_to_str(now_utc()), message.from_user.id))
        await db.commit()

    await message.answer("✅ Screenshot received. Admin approval ka wait karo.")

    username = f"@{message.from_user.username}" if message.from_user.username else "No username"
    caption = (
        "🧾 New payment screenshot\n\n"
        f"Payment ID: {payment_id}\n"
        f"User ID: {message.from_user.id}\n"
        f"Username: {username}\n"
        f"Plan: {plan['name']}\n"
        f"Amount: ₹{plan['amount']}"
    )
    for admin_id in ADMIN_IDS:
        await bot.send_photo(
            admin_id,
            file_id,
            caption=caption,
            reply_markup=approval_keyboard(payment_id, message.from_user.id)
        )


@dp.callback_query(F.data.startswith("approve:"))
async def approve_payment(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Admin only", show_alert=True)
        return

    _, payment_id_raw, user_id_raw = call.data.split(":")
    payment_id = int(payment_id_raw)
    user_id = int(user_id_raw)

    row = await get_user(user_id)
    if not row or not row["selected_plan"]:
        await call.answer("User/plan not found", show_alert=True)
        return

    plan_code = row["selected_plan"]
    plan = PLANS[plan_code]
    expiry = await activate_premium(user_id, plan_code)
    await set_payment_status(payment_id, "approved")

    invite = await bot.create_chat_invite_link(
        chat_id=PREMIUM_CHAT_ID,
        name=f"premium_{user_id}_{payment_id}",
        expire_date=now_utc() + timedelta(hours=24),
        member_limit=1,
    )

    await bot.send_message(
        user_id,
        "✅ Your premium is activated.\n\n"
        f"Plan: {plan['name']}\n"
        f"Expiry: {expiry.astimezone().strftime('%d-%m-%Y %I:%M %p')}\n\n"
        f"Private invite link, valid 24 hours and single-use:\n{invite.invite_link}"
    )
    await call.message.edit_caption((call.message.caption or "") + "\n\n✅ Approved")
    await call.answer("Approved")


@dp.callback_query(F.data.startswith("reject:"))
async def reject_payment(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Admin only", show_alert=True)
        return

    _, payment_id_raw, user_id_raw = call.data.split(":")
    payment_id = int(payment_id_raw)
    user_id = int(user_id_raw)
    await set_payment_status(payment_id, "rejected")

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET payment_status='rejected', updated_at=? WHERE user_id=?", (dt_to_str(now_utc()), user_id))
        await db.commit()

    await bot.send_message(user_id, f"❌ Payment rejected, please contact admin: {SUPPORT_USERNAME}")
    await call.message.edit_caption((call.message.caption or "") + "\n\n❌ Rejected")
    await call.answer("Rejected")


@dp.message(Command("users"))
async def users_cmd(message: Message):
    if not is_admin(message.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        total = (await cur.fetchone())[0]
    await message.answer(f"👥 Total users: {total}")


@dp.message(Command("premium_users"))
async def premium_users_cmd(message: Message):
    if not is_admin(message.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT user_id, username, selected_plan, premium_expiry FROM users WHERE access_status='active' ORDER BY premium_expiry ASC LIMIT 50")
        rows = await cur.fetchall()
    if not rows:
        await message.answer("No active premium users.")
        return
    text = "⭐ Active premium users:\n\n"
    for r in rows:
        username = f"@{r['username']}" if r['username'] else "-"
        text += f"{r['user_id']} | {username} | {r['selected_plan']} | {r['premium_expiry']}\n"
    await message.answer(text[:4000])


@dp.message(Command("addpremium"))
async def addpremium_cmd(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    args = (command.args or "").split()
    if len(args) != 2 or not args[0].isdigit() or not args[1].isdigit():
        await message.answer("Usage: /addpremium user_id days")
        return
    user_id = int(args[0])
    days = int(args[1])
    start = now_utc()
    expiry = start + timedelta(days=days)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        INSERT INTO users (user_id, selected_plan, payment_status, premium_start, premium_expiry, access_status, created_at, updated_at)
        VALUES (?, ?, 'approved', ?, ?, 'active', ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            selected_plan=excluded.selected_plan,
            payment_status='approved',
            premium_start=excluded.premium_start,
            premium_expiry=excluded.premium_expiry,
            access_status='active',
            updated_at=excluded.updated_at
        """, (user_id, f"manual_{days}d", dt_to_str(start), dt_to_str(expiry), dt_to_str(start), dt_to_str(start)))
        await db.commit()
    invite = await bot.create_chat_invite_link(
        chat_id=PREMIUM_CHAT_ID,
        name=f"manual_{user_id}",
        expire_date=now_utc() + timedelta(hours=24),
        member_limit=1,
    )
    await bot.send_message(user_id, f"✅ Premium manually activated for {days} days.\nInvite: {invite.invite_link}")
    await message.answer("Done.")


@dp.message(Command("removepremium"))
async def removepremium_cmd(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    args = (command.args or "").split()
    if len(args) != 1 or not args[0].isdigit():
        await message.answer("Usage: /removepremium user_id")
        return
    user_id = int(args[0])
    await remove_premium(user_id)
    try:
        await bot.ban_chat_member(PREMIUM_CHAT_ID, user_id)
        # Optional: unban immediately so user can rejoin later after renewal with a fresh invite.
        await bot.unban_chat_member(PREMIUM_CHAT_ID, user_id, only_if_banned=True)
    except Exception as e:
        logging.exception("Failed to remove user %s: %s", user_id, e)
    await message.answer("Premium removed.")


@dp.message(Command("broadcast"))
async def broadcast_cmd(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    text = command.args
    if not text:
        await message.answer("Usage: /broadcast message")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users")
        users = [r[0] for r in await cur.fetchall()]
    sent = 0
    for uid in users:
        try:
            await bot.send_message(uid, text)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await message.answer(f"Broadcast sent to {sent}/{len(users)} users.")


@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    if not is_admin(message.from_user.id):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        total = (await cur.fetchone())[0]
        cur = await db.execute("SELECT COUNT(*) FROM users WHERE access_status='active'")
        active = (await cur.fetchone())[0]
        cur = await db.execute("SELECT COUNT(*) FROM payments WHERE status='pending'")
        pending = (await cur.fetchone())[0]
    await message.answer(f"📊 Stats\nUsers: {total}\nActive premium: {active}\nPending payments: {pending}")


async def expiry_worker():
    while True:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute("""
                    SELECT user_id, premium_expiry FROM users
                    WHERE access_status='active' AND premium_expiry IS NOT NULL
                """)
                rows = await cur.fetchall()

            for row in rows:
                expiry = str_to_dt(row["premium_expiry"])
                if expiry and expiry <= now_utc():
                    user_id = int(row["user_id"])
                    try:
                        await bot.ban_chat_member(PREMIUM_CHAT_ID, user_id)
                        await bot.unban_chat_member(PREMIUM_CHAT_ID, user_id, only_if_banned=True)
                    except Exception as e:
                        logging.exception("Failed to expire user %s: %s", user_id, e)
                    await remove_premium(user_id)
                    try:
                        await bot.send_message(user_id, f"⏳ Your premium has expired. Renew with /renew")
                    except Exception:
                        pass
        except Exception as e:
            logging.exception("Expiry worker error: %s", e)

        await asyncio.sleep(3600)  # check every 1 hour

async def health_check(request):
    return web.Response(text="Bot is running")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)

    port = int(os.environ.get("PORT", 10000))
    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    print(f"Web server started on port {port}")
async def main():
    await init_db()
    await start_web_server()

    asyncio.create_task(expiry_worker())

    await dp.start_polling(bot)
    if __name__ == "__main__":
    asyncio.run(main())
