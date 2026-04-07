from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.db import UserRole, async_session_maker
from bot.handlers.export_stats import generate_stats_overview, get_allowed_dm_user_ids, get_requester
from bot.keyboards import get_buyer_keyboard

router = Router()


@router.callback_query(F.data == "buyer_stats")
async def buyer_stats_handler(callback: CallbackQuery):
    async with async_session_maker() as session:
        user = await get_requester(session, callback.from_user.id)
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        if (user.role or UserRole.DM.value) != UserRole.BUYER.value:
            await callback.answer("Недостаточно прав", show_alert=True)
            return

        dm_user_ids = await get_allowed_dm_user_ids(session, user)

    overview = await generate_stats_overview(dm_user_ids)

    text = (
        "Статистика байера\n\n"
        f"Всего чатов: {overview['chats']}\n"
        f"Сообщений всего: {overview['messages']}\n\n"
        f"Реги: {overview['rega']}\n"
        f"Депы: {overview['dep']}\n"
        f"До депы: {overview['do_dep']}\n"
    )

    if overview["rega"] > 0:
        text += f"\nКонверсия в деп: {overview['dep'] / overview['rega'] * 100:.1f}%"
    if overview["dep"] > 0:
        text += f"\nКонверсия в додеп: {overview['do_dep'] / overview['dep'] * 100:.1f}%"

    await callback.message.edit_text(text, reply_markup=get_buyer_keyboard())
    await callback.answer()
