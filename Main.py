# Main.py
import asyncio
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from Commands import register_handlers
from Function import init_db, ensure_dirs

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Set BOT_TOKEN in env")

DB_PATH = os.getenv("DB_PATH", "data/fintrack.db")

async def main():
    ensure_dirs(DB_PATH)
    await init_db(DB_PATH)

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
    )
    dp = Dispatcher()
    register_handlers(dp, db_path=DB_PATH)

    print("FinTrack bot is running...")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("FinTrack bot stopped")
