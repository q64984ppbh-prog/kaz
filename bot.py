import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TOKEN = '8731702089:AAHOAcCPSsbQBeYDqdizzxNO4mS8_uHfd4Q'
WEBAPP_URL = 'https://missile-civic-stops-enlarge.trycloudflare.com'

dp = Dispatcher()

@dp.message(Command('start'))
async def cmd_start(message: types.Message):
    logger.info(f"Received /start from user {message.from_user.id}")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Play TopGift (RU)",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    ]])
    await message.answer(
        "Welcome to TopGift! Start winning real Telegram Gifts right now!",
        reply_markup=keyboard
    )

async def main():
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
    logger.info("Bot started! Send /start to @TopGiftCrashBot")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
