from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, func
from bot.db import User, AccessRequest, RequestStatus, UserStatus, DMProfile, TGAccount, Chat, Event, EventSequence, EventType, async_session_maker
from bot.keyboards import get_dm_keyboard, get_dm_phrases_keyboard, get_greeting_msgs_edit_keyboard, get_dm_back_keyboard
from bot.handlers.dm_tg import save_tg_account
from bot.config import ADMIN_IDS
import logging

logger = logging.getLogger(__name__)
router = Router()


class PhraseStates(StatesGroup):
    waiting_for_greeting_1 = State()
    waiting_for_greeting_2 = State()
    waiting_for_dodep_keyword = State()
    waiting_for_tag = State()


@router.callback_query(F.data == "dm_back")
async def dm_back_handler(callback: CallbackQuery, state: FSMContext):
    """Возврат в меню ДМ"""
    await state.clear()

    tg_id = callback.from_user.id
    async with async_session_maker() as session:
        user_result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = user_result.scalar_one_or_none()

        if not user:
            await callback.answer("Ошибка: пользователь не найден", show_alert=True)
            return

        tg_result = await session.execute(select(TGAccount).where(TGAccount.user_id == user.id))
        tg_account = tg_result.scalar_one_or_none()

        dm_result = await session.execute(select(DMProfile).where(DMProfile.user_id == user.id))
        dm_profile = dm_result.scalar_one_or_none()

        has_profile = bool(dm_profile and dm_profile.tag)
        await callback.message.edit_text("Меню DM менеджера:", reply_markup=get_dm_keyboard(bool(tg_account), has_profile))

    await callback.answer()


@router.callback_query(F.data == "dm_my_stats")
async def dm_my_stats_handler(callback: CallbackQuery):
    """Личная статистика ДМ менеджера"""
    tg_id = callback.from_user.id

    async with async_session_maker() as session:
        # Находим пользователя
        user_result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = user_result.scalar_one_or_none()

        if not user:
            await callback.answer("Ошибка: пользователь не найден", show_alert=True)
            return

        # Находим TG аккаунт
        tg_result = await session.execute(select(TGAccount).where(TGAccount.user_id == user.id))
        tg_account = tg_result.scalar_one_or_none()

        if not tg_account:
            await callback.message.edit_text(
                "📊 Статистика\n\n"
                "❌ TG аккаунт не подключен.\n"
                "Подключите TG аккаунт для просмотра статистики.",
                reply_markup=get_dm_keyboard(False, False)
            )
            await callback.answer()
            return

        # Считаем события по этому TG аккаунту
        chats_result = await session.execute(select(Chat).where(Chat.tg_account_id == tg_account.id))
        chats = chats_result.scalars().all()
        chat_ids = [c.id for c in chats]

        rega_count = 0
        dep_count = 0
        dodep_count = 0
        total_messages = 0

        if chat_ids:
            rega_result = await session.execute(
                select(func.count(Event.id)).where(Event.chat_id.in_(chat_ids)).where(Event.event_type == EventType.REGA)
            )
            rega_count = rega_result.scalar() or 0

            dep_result = await session.execute(
                select(func.count(Event.id)).where(Event.chat_id.in_(chat_ids)).where(Event.event_type == EventType.DEP)
            )
            dep_count = dep_result.scalar() or 0

            dodep_result = await session.execute(
                select(func.count(Event.id)).where(Event.chat_id.in_(chat_ids)).where(Event.event_type == EventType.DO_DEP)
            )
            dodep_count = dodep_result.scalar() or 0

            for chat in chats:
                total_messages += chat.messages_count or 0

        text = "📊 Ваша статистика\n\n"
        text += f"Всего чатов: {len(chats)}\n"
        text += f"Сообщений всего: {total_messages}\n\n"
        text += f"✅ Рег: {rega_count}\n"
        text += f"✅ Депов: {dep_count}\n"
        text += f"✅ До депов: {dodep_count}\n\n"

        # Конверсия
        if rega_count > 0:
            text += f"Конверсия в деп: {dep_count / rega_count * 100:.1f}%\n"
        if dep_count > 0:
            text += f"Конверсия в додеп: {dodep_count / dep_count * 100:.1f}%"

        await callback.message.edit_text(text, reply_markup=get_dm_keyboard(True, True))

    await callback.answer()


@router.callback_query(F.data == "dm_edit_phrases")
async def dm_edit_phrases_handler(callback: CallbackQuery, state: FSMContext):
    """Редактирование фраз трекинга (сам ДМ)"""
    await callback.message.edit_text(
        "📝 Настройка фраз трекинга\n\n"
        "Здесь вы можете настроить сообщения для трекинга событий:\n"
        "1. Два сообщения после приветствия (для DEP)\n"
        "2. Ключевая фраза для DO_DEP",
        reply_markup=get_dm_phrases_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "dm_edit_greeting_msgs")
async def dm_edit_greeting_msgs_handler(callback: CallbackQuery):
    """Редактирование приветственных сообщений"""
    await callback.message.edit_text(
        "📝 Сообщения после приветствия\n\n"
        "Эти сообщения отправляются после того, как пользователь написал 'Вітаю'.\n"
        "После этих 2-х сообщений, если пользователь напишет ещё 4 сообщения - засчитается ДЕП.",
        reply_markup=get_greeting_msgs_edit_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "dm_edit_greeting_1")
async def dm_edit_greeting_1_handler(callback: CallbackQuery, state: FSMContext):
    """Редактирование первого приветственного сообщения"""
    tg_id = callback.from_user.id

    async with async_session_maker() as session:
        user_result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = user_result.scalar_one_or_none()

        if not user:
            await callback.answer("Ошибка: пользователь не найден", show_alert=True)
            return

        dm_result = await session.execute(select(DMProfile).where(DMProfile.user_id == user.id))
        dm_profile = dm_result.scalar_one_or_none()

        current = dm_profile.greeting_message1 if dm_profile and dm_profile.greeting_message1 else "Не задано"

    await state.set_state(PhraseStates.waiting_for_greeting_1)
    await callback.message.edit_text(
        f"✏️ Сообщение 1\n\n"
        f"Текущее: {current}\n\n"
        f"Отправьте новое сообщение:"
    )
    await callback.answer()


@router.message(PhraseStates.waiting_for_greeting_1)
async def process_greeting_1(message: Message, state: FSMContext):
    """Сохранение первого приветственного сообщения"""
    tg_id = message.from_user.id

    async with async_session_maker() as session:
        user_result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = user_result.scalar_one_or_none()

        if not user:
            await message.answer("Ошибка: пользователь не найден")
            await state.clear()
            return

        dm_result = await session.execute(select(DMProfile).where(DMProfile.user_id == user.id))
        dm_profile = dm_result.scalar_one_or_none()

        if dm_profile:
            dm_profile.greeting_message1 = message.text
        else:
            dm_profile = DMProfile(user_id=user.id, greeting_message1=message.text)
            session.add(dm_profile)

        await session.commit()

    await state.clear()
    await message.answer("✅ Сообщение 1 сохранено!", reply_markup=get_greeting_msgs_edit_keyboard())


@router.callback_query(F.data == "dm_edit_greeting_2")
async def dm_edit_greeting_2_handler(callback: CallbackQuery, state: FSMContext):
    """Редактирование второго приветственного сообщения"""
    tg_id = callback.from_user.id

    async with async_session_maker() as session:
        user_result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = user_result.scalar_one_or_none()

        if not user:
            await callback.answer("Ошибка: пользователь не найден", show_alert=True)
            return

        dm_result = await session.execute(select(DMProfile).where(DMProfile.user_id == user.id))
        dm_profile = dm_result.scalar_one_or_none()

        current = dm_profile.greeting_message2 if dm_profile and dm_profile.greeting_message2 else "Не задано"

    await state.set_state(PhraseStates.waiting_for_greeting_2)
    await callback.message.edit_text(
        f"✏️ Сообщение 2\n\n"
        f"Текущее: {current}\n\n"
        f"Отправьте новое сообщение:"
    )
    await callback.answer()


@router.message(PhraseStates.waiting_for_greeting_2)
async def process_greeting_2(message: Message, state: FSMContext):
    """Сохранение второго приветственного сообщения"""
    tg_id = message.from_user.id

    async with async_session_maker() as session:
        user_result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = user_result.scalar_one_or_none()

        if not user:
            await message.answer("Ошибка: пользователь не найден")
            await state.clear()
            return

        dm_result = await session.execute(select(DMProfile).where(DMProfile.user_id == user.id))
        dm_profile = dm_result.scalar_one_or_none()

        if dm_profile:
            dm_profile.greeting_message2 = message.text
        else:
            dm_profile = DMProfile(user_id=user.id, greeting_message2=message.text)
            session.add(dm_profile)

        await session.commit()

    await state.clear()
    await message.answer("✅ Сообщение 2 сохранено!", reply_markup=get_greeting_msgs_edit_keyboard())


@router.callback_query(F.data == "dm_edit_dodep_msg")
async def dm_edit_dodep_msg_handler(callback: CallbackQuery, state: FSMContext):
    """Редактирование ключевой фразы для додепа"""
    tg_id = callback.from_user.id

    async with async_session_maker() as session:
        user_result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = user_result.scalar_one_or_none()

        if not user:
            await callback.answer("Ошибка: пользователь не найден", show_alert=True)
            return

        dm_result = await session.execute(select(DMProfile).where(DMProfile.user_id == user.id))
        dm_profile = dm_result.scalar_one_or_none()

        current = dm_profile.dodep_keyword if dm_profile and dm_profile.dodep_keyword else "Не задано"

    await state.set_state(PhraseStates.waiting_for_dodep_keyword)
    await callback.message.edit_text(
        f"🎯 Ключевая фраза для DO_DEP\n\n"
        f"Текущая: {current}\n\n"
        f"Отправьте новую фразу. Когда вы отправите это сообщение пользователю (после DEP),\n"
        f"и он ответит - засчитается DO_DEP."
    )
    await callback.answer()


@router.message(PhraseStates.waiting_for_dodep_keyword)
async def process_dodep_keyword(message: Message, state: FSMContext):
    """Сохранение ключевой фразы для додепа"""
    tg_id = message.from_user.id

    async with async_session_maker() as session:
        user_result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = user_result.scalar_one_or_none()

        if not user:
            await message.answer("Ошибка: пользователь не найден")
            await state.clear()
            return

        dm_result = await session.execute(select(DMProfile).where(DMProfile.user_id == user.id))
        dm_profile = dm_result.scalar_one_or_none()

        if dm_profile:
            dm_profile.dodep_keyword = message.text
        else:
            dm_profile = DMProfile(user_id=user.id, dodep_keyword=message.text)
            session.add(dm_profile)

        await session.commit()

    await state.clear()
    await message.answer("✅ Ключевая фраза сохранена!", reply_markup=get_dm_phrases_keyboard())


@router.callback_query(F.data == "dm_fill_profile")
async def dm_fill_profile_handler(callback: CallbackQuery, state: FSMContext):
    """Создание профиля ДМ"""
    await state.set_state(PhraseStates.waiting_for_tag)
    await callback.message.edit_text("📝 Введите ваш тег (например, @manager):")
    await callback.answer()


@router.message(PhraseStates.waiting_for_tag)
async def process_tag(message: Message, state: FSMContext):
    """Сохранение тега ДМ"""
    tg_id = message.from_user.id

    async with async_session_maker() as session:
        user_result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = user_result.scalar_one_or_none()

        if not user:
            await message.answer("Ошибка: пользователь не найден")
            await state.clear()
            return

        dm_result = await session.execute(select(DMProfile).where(DMProfile.user_id == user.id))
        dm_profile = dm_result.scalar_one_or_none()

        if dm_profile:
            dm_profile.tag = message.text
        else:
            dm_profile = DMProfile(user_id=user.id, tag=message.text)
            session.add(dm_profile)

        await session.commit()

    await state.clear()
    await message.answer("✅ Профиль сохранен!", reply_markup=get_dm_keyboard(False, True))
