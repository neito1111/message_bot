from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func
from bot.db import User, AccessRequest, RequestStatus, UserStatus, DMProfile, TGAccount, Chat, Event, EventType, async_session_maker
from bot.keyboards import get_approve_keyboard, get_admin_keyboard, get_profile_keyboard, get_back_keyboard, get_dms_list_keyboard, get_dm_profile_keyboard, get_dm_keyboard, get_dm_phrases_admin_keyboard
import logging

logger = logging.getLogger(__name__)
router = Router()


def is_admin_user(tg_id: int) -> bool:
    """Проверяет, является ли пользователь админом"""
    from bot.config import ADMIN_IDS
    return tg_id in ADMIN_IDS


@router.callback_query(F.data == "admin_requests")
async def admin_requests_handler(callback: CallbackQuery):
    """Показ заявок на подтверждение (только не админы)"""
    async with async_session_maker() as session:
        # Получаем все pending заявки, кроме админов
        from bot.config import ADMIN_IDS
        result = await session.execute(
            select(AccessRequest)
            .where(AccessRequest.status == RequestStatus.PENDING)
            .join(User, AccessRequest.user_id == User.id)
            .where(User.tg_id.not_in(ADMIN_IDS))
        )
        requests = result.scalars().all()

    if not requests:
        await callback.message.edit_text("Нет новых запросов", reply_markup=get_admin_keyboard())
        await callback.answer()
        return

    text = f"Заявки на подтверждение: {len(requests)}\n\n"
    for req in requests:
        user_result = await session.execute(select(User).where(User.id == req.user_id))
        user = user_result.scalar_one_or_none()
        if user:
            text += f"User: {user.tg_id} (@{user.username or 'N/A'})\n"

    await callback.message.edit_text(text, reply_markup=get_admin_keyboard(len(requests)))
    await callback.answer()


@router.callback_query(F.data.startswith("approve_"))
async def approve_handler(callback: CallbackQuery):
    user_id = int(callback.data.split("_")[1])
    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.tg_id == user_id))
        user = result.scalar_one_or_none()

        if user:
            req_result = await session.execute(select(AccessRequest).where(AccessRequest.user_id == user.id))
            request = req_result.scalar_one_or_none()

            if request:
                request.status = RequestStatus.APPROVED
                request.processed_at = func.now()
                user.status = UserStatus.APPROVED
                await session.commit()

                await callback.message.edit_text(f"Пользователь {user_id} одобрен!")
                try:
                    await callback.bot.send_message(user.tg_id, "Ваш доступ одобрен! Используйте /start")
                except:
                    pass

    await callback.answer()


@router.callback_query(F.data.startswith("reject_"))
async def reject_handler(callback: CallbackQuery):
    user_id = int(callback.data.split("_")[1])
    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.tg_id == user_id))
        user = result.scalar_one_or_none()

        if user:
            req_result = await session.execute(select(AccessRequest).where(AccessRequest.user_id == user.id))
            request = req_result.scalar_one_or_none()

            if request:
                request.status = RequestStatus.REJECTED
                user.status = UserStatus.REJECTED
                await session.commit()
                await callback.message.edit_text(f"Пользователь {user_id} отклонен!")

    await callback.answer()


@router.callback_query(F.data == "admin_stats")
async def admin_stats_handler(callback: CallbackQuery):
    async with async_session_maker() as session:
        total_users = await session.execute(select(func.count(User.id)).where(User.status == UserStatus.APPROVED))
        total_users = total_users.scalar() or 0

        total_chats = await session.execute(select(func.count(Chat.id)))
        total_chats = total_chats.scalar() or 0

        rega = await session.execute(select(func.count(Event.id)).where(Event.event_type == EventType.REGA))
        rega = rega.scalar() or 0

        dep = await session.execute(select(func.count(Event.id)).where(Event.event_type == EventType.DEP))
        dep = dep.scalar() or 0

        do_dep = await session.execute(select(func.count(Event.id)).where(Event.event_type == EventType.DO_DEP))
        do_dep = do_dep.scalar() or 0

    text = f"Статистика:\n\n"
    text += f"Пользователей: {total_users}\n"
    text += f"Чатов: {total_chats}\n\n"
    text += f"Рег: {rega}\n"
    text += f"Депов: {dep}\n"
    text += f"До депов: {do_dep}"
    await callback.message.edit_text(text, reply_markup=get_back_keyboard())
    await callback.answer()


@router.callback_query(F.data == "admin_dms")
async def admin_dms_handler(callback: CallbackQuery):
    """Список DM пользователей (исключая админов)"""
    async with async_session_maker() as session:
        # Получаем всех одобренных пользователей, кроме админов
        from bot.config import ADMIN_IDS
        result = await session.execute(
            select(User)
            .where(User.status == UserStatus.APPROVED)
            .where(User.tg_id.not_in(ADMIN_IDS))
        )
        dms = result.scalars().all()

    if not dms:
        await callback.message.edit_text("Нет DM пользователей", reply_markup=get_admin_keyboard())
        await callback.answer()
        return

    dms_list = []
    for dm in dms:
        dms_list.append({
            "tg_id": dm.tg_id,
            "username": dm.username,
            "first_name": dm.first_name
        })

    await callback.message.edit_text(
        f"DM пользователи ({len(dms_list)}):\n\n" +
        "\n".join([f"👤 {d['username'] or d['tg_id']}" for d in dms_list]),
        reply_markup=get_dms_list_keyboard(dms_list)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("dm_profile_"))
async def dm_profile_handler(callback: CallbackQuery, state: FSMContext):
    """Профиль ДМ (просмотр для админа)"""
    tg_id = int(callback.data.split("_")[2])

    await state.update_data(target_dm_tg_id=tg_id)

    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalar_one_or_none()

        if not user:
            await callback.answer("Юзер не найден", show_alert=True)
            return

        dm_result = await session.execute(select(DMProfile).where(DMProfile.user_id == user.id))
        dm_profile = dm_result.scalar_one_or_none()

        tg_result = await session.execute(select(TGAccount).where(TGAccount.user_id == user.id))
        tg_account = tg_result.scalar_one_or_none()

        text = f"👤 Профиль DM\n\n"
        text += f"TG ID: {user.tg_id}\n"
        text += f"Username: @{user.username or 'N/A'}\n"
        text += f"Тег: {dm_profile.tag if dm_profile and dm_profile.tag else 'Не задан'}\n"
        text += f"TG аккаунт: {'✅ Подключен' if tg_account else '❌ Не подключен'}\n"

        # Показываем настроенные фразы
        if dm_profile:
            if dm_profile.greeting_message1:
                text += f"\n📝 Приветствие 1: {dm_profile.greeting_message1[:50]}..."
            if dm_profile.greeting_message2:
                text += f"\n📝 Приветствие 2: {dm_profile.greeting_message2[:50]}..."
            if dm_profile.dodep_keyword:
                text += f"\n🎯 Ключевая фраза: {dm_profile.dodep_keyword}"

        await callback.message.edit_text(text, reply_markup=get_dm_profile_keyboard(bool(tg_account)))

    await callback.answer()


@router.callback_query(F.data == "admin_back")
async def admin_back_handler(callback: CallbackQuery, state: FSMContext):
    """Возврат в меню админа"""
    await state.clear()

    async with async_session_maker() as session:
        result = await session.execute(
            select(AccessRequest).where(AccessRequest.status == RequestStatus.PENDING)
        )
        requests = result.scalars().all()
        count = len(requests)

    await callback.message.edit_text("Меню администратора:", reply_markup=get_admin_keyboard(count))
    await callback.answer()


@router.callback_query(F.data == "my_profile")
async def my_profile_handler(callback: CallbackQuery):
    """Профиль текущего пользователя (админа или ДМ)"""
    tg_id = callback.from_user.id
    is_admin = is_admin_user(tg_id)

    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalar_one_or_none()

        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        dm_result = await session.execute(select(DMProfile).where(DMProfile.user_id == user.id))
        dm_profile = dm_result.scalar_one_or_none()

        tg_result = await session.execute(select(TGAccount).where(TGAccount.user_id == user.id))
        tg_account = tg_result.scalar_one_or_none()

        if is_admin:
            # Меню админа
            text = "👑 Профиль администратора\n"
            text += f"TG ID: {user.tg_id}\n"
            text += f"Username: @{user.username or 'N/A'}\n"
            await callback.message.edit_text(text, reply_markup=get_profile_keyboard(bool(dm_profile), is_admin=True))
        else:
            # Меню ДМ
            text = "👤 Ваш профиль DM\n"
            text += f"Статус: {user.status.value}\n"
            text += f"TG аккаунт: {'✅ Подключен' if tg_account else '❌ Не подключен'}\n"

            if dm_profile:
                if dm_profile.tag:
                    text += f"Тег: {dm_profile.tag}\n"
                if dm_profile.greeting_message1:
                    text += f"Приветствие 1: настроено\n"
                if dm_profile.greeting_message2:
                    text += f"Приветствие 2: настроено\n"
                if dm_profile.dodep_keyword:
                    text += f"Ключевая фраза: настроена\n"

            has_profile = bool(dm_profile and dm_profile.tag)
            await callback.message.edit_text(text, reply_markup=get_dm_keyboard(bool(tg_account), has_profile))

    await callback.answer()


@router.callback_query(F.data == "dm_edit_phrases_admin")
async def dm_edit_phrases_admin_handler(callback: CallbackQuery, state: FSMContext):
    """Редактирование фраз ДМ (из профиля админом)"""
    data = await state.get_data()
    target_dm_tg_id = data.get("target_dm_tg_id")

    if not target_dm_tg_id:
        await callback.answer("Ошибка: не выбран пользователь", show_alert=True)
        return

    await callback.message.edit_text(
        "📝 Настройка фраз трекинга\n\n"
        "Здесь вы можете настроить сообщения для трекинга событий:\n"
        "1. Два сообщения после приветствия (для DEP)\n"
        "2. Ключевая фраза для DO_DEP",
        reply_markup=get_dm_phrases_admin_keyboard()
    )
    await callback.answer()
