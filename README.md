# Telegram Premium Subscription Bot Starter

Manual screenshot approval subscription bot using Python + aiogram + SQLite.

## Features
- /start plans with buttons
- Manual payment screenshot submission
- Admin approval panel with Approve / Reject
- Single-use, limited-time private invite link after approval
- Expiry date stored by plan
- Auto remove/ban expired users
- Admin commands: /users, /premium_users, /addpremium, /removepremium, /broadcast, /stats
- User commands: /myplan, /renew, /help

## Setup
1. Create bot from @BotFather and copy token.
2. Add bot as admin in premium private channel/group.
3. Give bot permission to invite users and ban/remove users.
4. Get chat ID of premium channel/group, usually like -100xxxxxxxxxx.
5. Copy `.env.example` to `.env` and fill values.
6. Install dependencies:
   pip install -r requirements.txt
7. Run:
   python bot.py

## Render trial deploy
Use a Background Worker or Web Service that runs continuously.
Build command:
   pip install -r requirements.txt
Start command:
   python bot.py

For production, use PostgreSQL or attach persistent disk. SQLite can be lost on redeploy if storage is ephemeral.
