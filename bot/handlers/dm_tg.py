from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from bot.client import tg_manager
from bot.db import User, TGAccount, async_session_maker
from bot.keyboards import get_back_keyboard, get_dm_profile_keyboard, get_admin_keyboard, get_dm_keyboard, get_tg_auth_start_keyboard
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
import logging

logger = logging.getLogger(__name__)
router = Router()

class TGAuthStates(StatesGroup):
    waiting_for_api_id = State()
    waiting_for_api_hash = State()
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_password = State()

def get_resend_keyboard(is_admin=False):
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Отправить код", callback_data="resend_code")
    builder.button(text="🔙 Назад", callback_data="admin_back" if is_admin else "dm_back_to_profile")
    return builder.as_markup()

def get_back_kb(is_admin=False):
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="admin_back" if is_admin else "dm_back_to_profile")
    return builder.as_markup()

async def get_current_user_tg_id(state: FSMContext) -> int:
    """Получает tg_id текущего пользователя или из состояния"""
    data = await state.get_data()
    target = data.get("target_dm_tg_id")
    if target:
        return target
    # Для собственного профиля
    return None

async def get_menu_keyboard(is_admin: bool, has_profile: bool = False) -> InlineKeyboardMarkup:
    """Возвращает правильную клавиатуру для меню"""
    if is_admin:
        return get_admin_keyboard()
    else:
        return get_dm_keyboard(has_tg=True, has_profile=has_profile)


@router.callback_query(F.data == "dm_add_tg")
async def dm_add_tg_start(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    target_dm_tg_id = data.get("target_dm_tg_id")

    # Если нет target, значит подключаем для себя
    if not target_dm_tg_id:
        target_dm_tg_id = callback.from_user.id

    await state.update_data(target_dm_tg_id=target_dm_tg_id)
    await state.set_state(TGAuthStates.waiting_for_api_id)
    await callback.message.edit_text(
        "🔐 TG Авторизация\n\n"
        "Шаг 1/5: API_ID (число):\n"
        "https://my.telegram.org/apps"
    )
    await callback.answer()


@router.message(TGAuthStates.waiting_for_api_id)
async def process_api_id(message: Message, state: FSMContext):
    try:
        api_id = int(message.text.strip())
        await state.update_data(api_id=api_id)
        await state.set_state(TGAuthStates.waiting_for_api_hash)
        await message.answer("Шаг 2/5: API_HASH (строка):")
    except:
        await message.answer("❌ Введите число")


@router.message(TGAuthStates.waiting_for_api_hash)
async def process_api_hash(message: Message, state: FSMContext):
    api_hash = message.text.strip()
    if len(api_hash) < 10:
        await message.answer("❌ Некорректный API_HASH")
        return
    await state.update_data(api_hash=api_hash)
    await state.set_state(TGAuthStates.waiting_for_phone)
    await message.answer("Шаг 3/5: Телефон (+380...):")


@router.message(TGAuthStates.waiting_for_phone)
async def process_phone(message: Message, state: FSMContext):
    phone = message.text.strip()
    if not phone.startswith("+"):
        await message.answer("❌ Телефон с +")
        return

    await state.update_data(phone=phone)
    data = await state.get_data()
    api_id = data.get("api_id", 2040)
    api_hash = data.get("api_hash", "b18441a1ff607e28a9a1686bc0540751")

    if api_id == 0 or not api_hash:
        api_id = 2040
        api_hash = "b18441a1ff607e28a9a1686bc0540751"
        await state.update_data(api_id=api_id, api_hash=api_hash)

    await message.answer("🔄 Подключение...")

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.connect()

        result = await client.send_code_request(phone)

        await state.update_data(
            phone_code_hash=result.phone_code_hash,
            api_id=api_id,
            api_hash=api_hash
        )
        await state.set_state(TGAuthStates.waiting_for_code)

        session_str = client.session.save()
        await state.update_data(auth_session=session_str)

        await message.answer(
            f"✅ Код отправлен на {phone}!\n\n"
            "Шаг 4/5: Введите код из Telegram\n\n"
            "Если код не пришел - нажмите кнопку",
            reply_markup=get_resend_keyboard()
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}", reply_markup=get_back_kb())
        logger.error(f"Send code: {e}")


@router.callback_query(F.data == "resend_code")
async def resend_code(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    phone = data.get("phone")
    api_id = data.get("api_id", 2040)
    api_hash = data.get("api_hash", "b18441a1ff607e28a9a1686bc0540751")

    if not phone:
        await callback.answer("Ошибка: нет телефона", show_alert=True)
        return

    await callback.message.edit_text("🔄 Отправка кода...")

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.connect()

        result = await client.send_code_request(phone)

        await state.update_data(
            phone_code_hash=result.phone_code_hash,
            auth_session=client.session.save()
        )

        await callback.message.edit_text(
            f"✅ Код отправлен на {phone}!\n\n"
            "Введите код:",
            reply_markup=get_resend_keyboard()
        )
    except Exception as e:
        await callback.message.edit_text(f"❌ {e}", reply_markup=get_back_kb())

    await callback.answer()


@router.message(TGAuthStates.waiting_for_code)
async def process_code(message: Message, state: FSMContext):
    code = message.text.strip()
    data = await state.get_data()

    phone = data.get("phone")
    phone_code_hash = data.get("phone_code_hash")
    api_id = data.get("api_id", 2040)
    api_hash = data.get("api_hash", "b18441a1ff607e28a9a1686bc0540751")
    target_dm_tg_id = data.get("target_dm_tg_id")
    auth_session = data.get("auth_session")

    logger.info(f"Code verify: phone={phone}, hash={phone_code_hash}, code={code}, target={target_dm_tg_id}")

    if not phone or not phone_code_hash:
        await message.answer("❌ Ошибка данных. Начните заново.", reply_markup=get_back_kb())
        return

    await message.answer("🔄 Проверка...")

    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.errors import SessionPasswordNeededError

    if auth_session:
        client = TelegramClient(StringSession(auth_session), api_id, api_hash)
    else:
        client = TelegramClient(StringSession(), api_id, api_hash)

    await client.connect()

    try:
        await client.sign_in(
            phone=phone,
            code=code,
            phone_code_hash=phone_code_hash
        )
    except SessionPasswordNeededError:
        await state.set_state(TGAuthStates.waiting_for_password)
        session_string = client.session.save()
        await state.update_data(
            temp_session=session_string,
            api_id=api_id,
            api_hash=api_hash,
            phone=phone,
            phone_code_hash=phone_code_hash,
            target_dm_tg_id=target_dm_tg_id
        )
        await message.answer(
            "🔐 Введите 2FA пароль\n\n"
            "Шаг 5/5: Cloud Password:",
            reply_markup=get_back_kb()
        )
        return
    except Exception as e:
        err = str(e)
        logger.error(f"Sign in error: {err}")
        if "CODE_INVALID" in err.upper() or "PHONE_CODE_INVALID" in err.upper():
            await message.answer("❌ Неверный код!", reply_markup=get_resend_keyboard())
        elif "CODE_EXPIRED" in err.upper() or "PHONE_CODE_EXPIRED" in err.upper():
            await message.answer("❌ Код истек!", reply_markup=get_resend_keyboard())
        else:
            await message.answer(f"❌ {err}", reply_markup=get_resend_keyboard())
        await client.disconnect()
        return

    # Успешный вход без 2FA
    session_string = client.session.save()
    await client.disconnect()

    # Сохраняем аккаунт
    target = target_dm_tg_id or message.from_user.id
    await save_tg_account(target, session_string, phone, api_id=api_id, api_hash=api_hash)
    await state.clear()

    text = f"✅ TG аккаунт подключен!\n\n"
    text += f"📱 Телефон: {phone}\n"
    text += f"👤 DM: @{target}\n\n"
    text += "Можно продолжать работу"

    await message.answer(text, reply_markup=get_back_kb())


@router.message(TGAuthStates.waiting_for_password)
async def process_password(message: Message, state: FSMContext):
    password = message.text.strip()
    data = await state.get_data()

    phone = data.get("phone")
    phone_code_hash = data.get("phone_code_hash")
    api_id = data.get("api_id", 2040)
    api_hash = data.get("api_hash", "b18441a1ff607e28a9a1686bc0540751")
    target_dm_tg_id = data.get("target_dm_tg_id")
    temp_session = data.get("temp_session")

    await message.answer("🔄 Проверка пароля...")

    from telethon import TelegramClient
    from telethon.sessions import StringSession

    if temp_session:
        client = TelegramClient(StringSession(temp_session), api_id, api_hash)
    else:
        client = TelegramClient(StringSession(), api_id, api_hash)

    await client.connect()

    try:
        await client.sign_in(password=password)
        session_string = client.session.save()
        await client.disconnect()

        # Сохраняем аккаунт
        target = target_dm_tg_id or message.from_user.id
        await save_tg_account(target, session_string, phone, api_id=api_id, api_hash=api_hash)
        await state.clear()

        text = f"✅ TG аккаунт подключен!\n\n"
        text += f"📱 {phone}\n"
        text += f"👤 DM: @{target}"
        await message.answer(text, reply_markup=get_back_kb())

    except Exception as e:
        await message.answer(f"❌ Ошибка 2FA пароля: {e}", reply_markup=get_back_kb())
        logger.error(f"Password error: {e}")


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


async def save_tg_account(target_dm_tg_id, session_string, phone, api_id=None, api_hash=None):
    """Сохраняет TG аккаунт для пользователя"""
    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.tg_id == target_dm_tg_id))
        user = result.scalar_one_or_none()

        if not user:
            logger.error(f"User {target_dm_tg_id} not found")
            return

        existing = await session.execute(select(TGAccount).where(TGAccount.user_id == user.id))
        tg = existing.scalar_one_or_none()

        if tg:
            tg.session_string = session_string
            tg.phone = phone
            tg.api_id = api_id
            tg.api_hash = api_hash
            tg.is_active = 1
        else:
            tg = TGAccount(
                user_id=user.id,
                phone=phone,
                api_id=api_id,
                api_hash=api_hash,
                session_string=session_string,
                is_active=1,
            )
            session.add(tg)

        await session.commit()
        logger.info(f"✅ TG account saved for user {target_dm_tg_id}")
        user_id = user.id

    await tg_manager.refresh_account(user_id)
