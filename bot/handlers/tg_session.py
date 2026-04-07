from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from bot.db import User, TGAccount, UserStatus, async_session_maker
from bot.keyboards import get_back_keyboard
import logging

logger = logging.getLogger(__name__)
router = Router()

class TGAuthStates(StatesGroup):
    waiting_for_api_id = State()
    waiting_for_api_hash = State()
    waiting_for_phone = State()
    waiting_for_password = State()
    waiting_for_cloud_password = State()
    waiting_for_code = State()

@router.callback_query(F.data == "add_tg_account")
async def add_tg_account_start(callback: CallbackQuery, state: FSMContext):
    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.tg_id == callback.from_user.id))
        user = result.scalar_one_or_none()

        if not user or user.status != UserStatus.APPROVED:
            await callback.answer("Доступ запрещен", show_alert=True)
            return

        existing = await session.execute(select(TGAccount).where(TGAccount.user_id == user.id))
        if existing.scalar_one_or_none():
            await callback.message.edit_text("У вас уже есть подключенный TG аккаунт.")
            return

    await state.set_state(TGAuthStates.waiting_for_api_id)
    await callback.message.edit_text(
        "Подключение TG аккаунта\n\n"
        "Шаг 1/6: Введите API_ID от https://my.telegram.org/apps\n\n"
        "Если нет credentials, введите 0 для стандартных."
    )
    await callback.answer()

@router.message(TGAuthStates.waiting_for_api_id)
async def process_api_id(message: Message, state: FSMContext):
    try:
        api_id = int(message.text)
        await state.update_data(api_id=api_id)
    except ValueError:
        await message.answer("Введите корректное число")
        return

    await state.set_state(TGAuthStates.waiting_for_api_hash)
    await message.answer("Шаг 2/6: Введите API_HASH от https://my.telegram.org/apps")

@router.message(TGAuthStates.waiting_for_api_hash)
async def process_api_hash(message: Message, state: FSMContext):
    await state.update_data(api_hash=message.text.strip())
    await state.set_state(TGAuthStates.waiting_for_phone)
    await message.answer("Шаг 3/6: Введите ваш номер телефона (например: +380123456789)")

@router.message(TGAuthStates.waiting_for_phone)
async def process_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    if not phone.startswith("+"):
        await message.answer("Номер должен начинаться с +")
        return

    await state.update_data(phone=phone)
    await state.set_state(TGAuthStates.waiting_for_password)
    await message.answer("Шаг 4/6: Введите пароль от TG аккаунта\n\nВведите 'nopassword' если нет 2FA.")

@router.message(TGAuthStates.waiting_for_password)
async def process_password(message: Message, state: FSMContext):
    await state.update_data(password=message.text.strip())
    await state.set_state(TGAuthStates.waiting_for_cloud_password)
    await message.answer("Шаг 5/6: Введите Cloud Password (2FA)\n\nВведите 'nopassword' если нет.")

@router.message(TGAuthStates.waiting_for_cloud_password)
async def process_cloud_password(message: Message, state: FSMContext):
    cloud_password = message.text.strip()
    data = await state.get_data()

    api_id = data.get("api_id", 0)
    api_hash = data.get("api_hash", "")
    phone = data.get("phone", "")
    password = data.get("password", "")

    if api_id == 0 or not api_hash:
        api_id = 2040
        api_hash = "b18441a1ff607e28a9a1686bc0540751"

    await state.update_data(cloud_password=cloud_password)
    await state.set_state(TGAuthStates.waiting_for_code)
    await message.answer("Шаг 6/6: Введите код подтверждения из TG")

    await state.update_data({
        "api_id": api_id,
        "api_hash": api_hash
    })

@router.message(TGAuthStates.waiting_for_code)
async def process_code(message: Message, state: FSMContext):
    code = message.text.strip()
    data = await state.get_data()

    phone = data.get("phone")
    password = data.get("password")
    cloud_password = data.get("cloud_password")
    api_id = data.get("api_id")
    api_hash = data.get("api_hash")

    await message.answer("Подключение к TG...")

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        async with TelegramClient(StringSession(), api_id, api_hash) as client:
            await client.sign_in(
                phone=phone,
                code=code,
                password=password if password != "nopassword" else None,
            )

            session_string = client.session.save()

            async with async_session_maker() as session:
                result = await session.execute(select(User).where(User.tg_id == message.from_user.id))
                user = result.scalar_one_or_none()

                if not user:
                    await message.answer("Пользователь не найден")
                    return

                tg_account = TGAccount(
                    user_id=user.id,
                    phone=phone,
                    session_string=session_string,
                    is_active=1
                )
                session.add(tg_account)
                await session.commit()

            me = await client.get_me()
            await state.clear()
            await message.answer(
                f"TG аккаунт подключен!\n\n"
                f"Номер: {me.phone}\n"
                f"Имя: {me.first_name}\n\n"
                "Бот будет отслеживать сообщения в этом аккаунте."
            )

    except Exception as e:
        await message.answer(f"Ошибка: {e}")
        logger.error(f"TG auth error: {e}")

@router.callback_query(F.data == "tg_back")
async def tg_back_handler(callback: CallbackQuery):
    await callback.message.edit_text("Меню:", reply_markup=get_back_keyboard())
    await callback.answer()
