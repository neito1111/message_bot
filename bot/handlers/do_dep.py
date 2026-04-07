from aiogram import Router, F, types
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from bot.db import User, TGAccount, DMProfile, Chat, Event, EventType, ChatStatus, async_session_maker
import logging
import json

logger = logging.getLogger(__name__)
router = Router()

DO_DEP_TRIGGER = "Як отримаєте виплату, будь ласка вiдправте скрiн що кошти прийшли\nТа якийсь скромний вiдгук вiд вас"

@router.message(F.text.contains("Як отримаєте виплату"))
async def process_do_dep_trigger(message: Message):
    """Фиксируем ДО ДЕП когда ДМ отправляет триггерное сообщение"""
    async with async_session_maker() as session:
        # Проверяем, что сообщение от пользователя с TG аккаунтом
        result = await session.execute(
            select(TGAccount).join(User).where(User.tg_id == message.from_user.id)
        )
        tg_account = result.scalar_one_or_none()

        if not tg_account:
            return

        # Ищем чат, где было отправлено сообщение
        chat_result = await session.execute(
            select(Chat).where(
                Chat.tg_account_id == tg_account.id,
                Chat.chat_id == message.chat.id
            )
        )
        chat = chat_result.scalar_one_or_none()

        if not chat or chat.status != ChatStatus.DEP_DONE:
            return

        # Фиксируем ДО ДЕП событие
        event_obj = Event(
            chat_id=chat.id,
            event_type=EventType.DO_DEP,
            event_data=json.dumps({
                "trigger_message": message.text,
                "message_id": message.message_id
            })
        )
        session.add(event_obj)

        chat.status = ChatStatus.DO_DEP_DONE
        await session.commit()

        logger.info(f"DO_DEP: Зафиксировано в чате {chat.chat_id}")

        # Уведомляем админа
        from bot.config import ADMIN_IDS
        for admin_id in ADMIN_IDS:
            try:
                await message.bot.send_message(
                    admin_id,
                    f"✅ ДО ДЕП зафиксирован!\n\n"
                    f"Чат: {chat.chat_title}\n"
                    f"ДМ: @{message.from_user.username}"
                )
            except:
                pass

@router.callback_query(F.data.startswith("chat_info_"))
async def chat_info_handler(callback: CallbackQuery):
    chat_id = int(callback.data.split("_")[2])

    async with async_session_maker() as session:
        chat = await session.get(Chat, chat_id)

        if not chat:
            await callback.answer("Чат не найден", show_alert=True)
            return

        events_result = await session.execute(
            select(Event).where(Event.chat_id == chat.id).order_by(Event.created_at)
        )
        events = events_result.scalars().all()

        events_text = ""
        for e in events:
            events_text += f"\n- {e.event_type.value} - {e.created_at.strftime('%d.%m %H:%M')}"

        text = f"""
💬 Информация о чате

Клиент: {chat.user_first_name} @{chat.user_username or 'нет'}
TG ID: {chat.user_tg_id}
Статус: {chat.status.value}

Сообщений: {chat.messages_count}

События:{events_text if events_text else ' нет'}
"""

    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("chat_events_"))
async def chat_events_handler(callback: CallbackQuery):
    chat_id = int(callback.data.split("_")[2])

    async with async_session_maker() as session:
        events_result = await session.execute(
            select(Event).where(Event.chat_id == chat_id).order_by(Event.created_at.desc())
        )
        events = events_result.scalars().all()

        if not events:
            await callback.answer("Нет событий", show_alert=True)
            return

        text = "📝 События чата:\n\n"
        for i, e in enumerate(events, 1):
            data = json.loads(e.event_data) if e.event_data else {}
            text += f"{i}. {e.event_type.value}\n"
            text += f"   Дата: {e.created_at.strftime('%d.%m.%Y %H:%M')}\n"
            if data.get("username"):
                text += f"   Юзер: @{data.get('username')}\n"
            text += "\n"

    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()
