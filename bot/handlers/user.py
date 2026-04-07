from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from bot.db import User, AccessRequest, RequestStatus, UserStatus, DMProfile, TGAccount, async_session_maker
from bot.keyboards import get_start_keyboard, get_admin_keyboard, get_dm_keyboard, get_profile_keyboard, get_back_keyboard, get_dm_back_keyboard
from bot.config import ADMIN_IDS
import logging

logger = logging.getLogger(__name__)
router = Router()

# Импортируем get_approve_keyboard в конце файла
from bot.keyboards import get_approve_keyboard


def is_admin_user(tg_id: int) -> bool:
    """Проверяет, является ли пользователь админом"""
    return tg_id in ADMIN_IDS


class ProfileStates(StatesGroup):
    waiting_for_tag = State()
    waiting_for_message1 = State()
    waiting_for_message2 = State()


@router.message(Command("start"))
async def start_handler(message: Message):
    """Обработчик /start - разделяет меню админа и DM"""
    tg_id = message.from_user.id

    # СРАЗУ проверяем, является ли пользователь админом
    if is_admin_user(tg_id):
        async with async_session_maker() as session:
            result = await session.execute(select(User).where(User.tg_id == tg_id))
            user = result.scalar_one_or_none()

            if not user:
                user = User(
                    tg_id=tg_id,
                    username=message.from_user.username,
                    first_name=message.from_user.first_name,
                    status=UserStatus.APPROVED
                )
                session.add(user)
                await session.commit()
            elif user.status != UserStatus.APPROVED:
                user.status = UserStatus.APPROVED
                await session.commit()

        await message.answer("👑 Меню администратора:", reply_markup=get_admin_keyboard())
        return

    # Для НЕ админов - проверяем статус
    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalar_one_or_none()

        if not user:
            user = User(
                tg_id=tg_id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                status=UserStatus.PENDING
            )
            session.add(user)
            await session.commit()

        if user.status == UserStatus.APPROVED:
            # Меню DM менеджера
            tg_result = await session.execute(select(TGAccount).where(TGAccount.user_id == user.id))
            tg_account = tg_result.scalar_one_or_none()

            dm_result = await session.execute(select(DMProfile).where(DMProfile.user_id == user.id))
            dm_profile = dm_result.scalar_one_or_none()

            has_profile = bool(dm_profile and dm_profile.tag)
            await message.answer("📱 Меню DM менеджера:", reply_markup=get_dm_keyboard(bool(tg_account), has_profile))
        elif user.status == UserStatus.REJECTED:
            await message.answer("Ваш доступ отклонен.")
        else:
            await message.answer("Ожидание подтверждения:", reply_markup=get_start_keyboard())


@router.callback_query(F.data == "send_request")
async def send_request_handler(callback: CallbackQuery):
    """Отправка заявки на доступ (не для админов)"""
    # Админы не могут отправлять заявки
    if is_admin_user(callback.from_user.id):
        await callback.answer("Администраторы не нуждаются в заявке", show_alert=True)
        return

    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.tg_id == callback.from_user.id))
        user = result.scalar_one_or_none()

        if not user:
            user = User(tg_id=callback.from_user.id, username=callback.from_user.username)
            session.add(user)
            await session.commit()

        existing = await session.execute(select(AccessRequest).where(AccessRequest.user_id == user.id))
        if existing.scalar_one_or_none():
            await callback.answer("Заявка уже отправлена", show_alert=True)
            return

        request = AccessRequest(user_id=user.id, status=RequestStatus.PENDING)
        session.add(request)
        user.status = UserStatus.PENDING
        await session.commit()

        # Уведомляем админов
        for admin_id in ADMIN_IDS:
            try:
                await callback.bot.send_message(
                    admin_id,
                    f"📩 Новая заявка от @{callback.from_user.username}\nID: {callback.from_user.id}",
                    reply_markup=get_approve_keyboard(callback.from_user.id)
                )
            except:
                pass

        await callback.message.edit_text("Заявка отправлена! Ожидайте подтверждения.")
    await callback.answer()


@router.callback_query(F.data == "fill_profile")
async def fill_profile_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ProfileStates.waiting_for_tag)
    await callback.message.edit_text("✏️ Введите ваш тег для трекинга (например, @manager):")
    await callback.answer()


@router.message(ProfileStates.waiting_for_tag)
async def process_tag(message: Message, state: FSMContext):
    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.tg_id == message.from_user.id))
        user = result.scalar_one_or_none()

        if user:
            existing = await session.execute(select(DMProfile).where(DMProfile.user_id == user.id))
            dm = existing.scalar_one_or_none()

            if dm:
                dm.tag = message.text
            else:
                dm = DMProfile(user_id=user.id, tag=message.text)
                session.add(dm)
            await session.commit()

    # Возвращаемся в меню DM
    tg_result = await session.execute(select(TGAccount).where(TGAccount.user_id == user.id))
    tg_account = tg_result.scalar_one_or_none()

    await state.clear()
    await message.answer("✅ Профиль сохранен!", reply_markup=get_dm_keyboard(bool(tg_account), True))


@router.callback_query(F.data == "dm_back_to_profile")
async def dm_back_to_profile_handler(callback: CallbackQuery, state: FSMContext):
    """Возврат в профиль из TG авторизации"""
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
