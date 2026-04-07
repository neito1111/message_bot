from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy import select
import logging

from bot.config import BOT_TOKEN, LOG_LEVEL, ADMIN_IDS
from bot.db import init_db, TGAccount, User, UserStatus, AccessRequest, RequestStatus, async_session_maker
from bot.client import tg_manager
from bot.handlers import admin, user, dm_tg, export_stats, dm_user

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Register routers
dp.include_router(user.router)
dp.include_router(admin.router)
dp.include_router(dm_tg.router)
dp.include_router(export_stats.router)
dp.include_router(dm_user.router)

async def on_startup():
    logger.info("Bot starting...")
    await init_db()
    logger.info("Database initialized")

    # Auto-approve admins
    async with async_session_maker() as session:
        for admin_id in ADMIN_IDS:
            result = await session.execute(select(User).where(User.tg_id == admin_id))
            user = result.scalar_one_or_none()
            if not user:
                user = User(tg_id=admin_id, username="admin", first_name="Admin", status=UserStatus.APPROVED)
                session.add(user)
                await session.flush()
                session.add(AccessRequest(user_id=user.id, status=RequestStatus.APPROVED))
                await session.commit()
                logger.info(f"Admin {admin_id} auto-approved")
            elif user.status != UserStatus.APPROVED:
                user.status = UserStatus.APPROVED
                await session.commit()
                logger.info(f"Admin {admin_id} status updated")

    await tg_manager.start()
    logger.info("TG workers started")

    logger.info(f"Bot started. Admins: {ADMIN_IDS}")

async def on_shutdown():
    logger.info("Bot stopping...")
    await tg_manager.stop()
    await bot.session.close()
    logger.info("Bot stopped")

async def start_polling():
    await on_startup()
    try:
        await dp.start_polling(bot)
    finally:
        await on_shutdown()
