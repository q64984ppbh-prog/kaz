import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TOKEN = '8731702089:AAHOAcCPSsbQBeYDqdizzxNO4mS8_uHfd4Q'
WEBAPP_URL = 'https://creator-buys-salem-labs.trycloudflare.com'
ADMIN_USER_ID = 8374183799

dp = Dispatcher()

@dp.message(Command('start'))
async def cmd_start(message: types.Message):
    logger.info(f"Received /start from user {message.from_user.id}")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Play",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    ]])
    await message.answer(
        "<b>Welcome to UP! №1 Crash Game in Telegram</b>",
        reply_markup=keyboard
    )


@dp.message(Command('admin'))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_USER_ID:
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Админ-действия", callback_data="admin_help")
    ]])
    await message.answer(
        "<b>Админ панель</b>\nКоманды: /admin, /start. Управление перенесено из мини-аппки в бота.",
        reply_markup=keyboard
    )

async def main():
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode='HTML'))
    logger.info("Bot started! Send /start to @TopGiftCrashBot")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
