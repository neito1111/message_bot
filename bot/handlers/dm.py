from aiogram import Router, F, types
from aiogram.types import CallbackQuery
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from bot.db import (
    User, TGAccount, DMProfile, Chat, Event, EventType, ChatStatus,
    async_session_maker
)
from bot.client import tg_manager
from telethon import events
import logging
import json

logger = logging.getLogger(__name__)
router = Router()

# Глобальный словарь для отслеживания чатов
active_chats: dict[int, dict] = {}

async def setup_dm_listeners():
    """Настраиваем слушатели для всех активных TG аккаунтов"""
    async with async_session_maker() as session:
        result = await session.execute(select(TGAccount).where(TGAccount.is_active == 1))
        accounts = result.scalars().all()

    for account in accounts:
        client = tg_manager.get_client(account.user_id)
        if client:
            setup_chat_listener(account, client)

def setup_chat_listener(account, client):
    """Настраиваем слушатель чатов для аккаунта"""

    @client.on(events.NewMessage(incoming=True))
    async def handler(event):
        try:
            chat = await event.get_chat()
            sender = await event.get_sender()
            message = event.message

            # Игнорируем ботов и себя
            if sender.bot or sender.id == account.user_id:
                return

            async with async_session_maker() as session:
                # Ищем или создаем чат
                result = await session.execute(
                    select(Chat).where(
                        Chat.tg_account_id == account.id,
                        Chat.chat_id == chat.id
                    )
                )
                existing_chat = result.scalar_one_or_none()

                if not existing_chat:
                    # Новый чат - фиксируем РЕГУ
                    new_chat = Chat(
                        tg_account_id=account.id,
                        chat_id=chat.id,
                        chat_title=getattr(chat, 'title', None) or getattr(chat, 'first_name', ''),
                        user_first_name=getattr(sender, 'first_name', ''),
                        user_username=getattr(sender, 'username', None),
                        user_tg_id=sender.id,
                        messages_count=1,
                        first_message_text=message.text or ""
                    )
                    session.add(new_chat)

                    # Логируем событие РЕГА
                    event_obj = Event(
                        chat_id=new_chat.id,
                        event_type=EventType.REGA,
                        event_data=json.dumps({
                            "user_id": sender.id,
                            "username": getattr(sender, 'username', ''),
                            "first_name": getattr(sender, 'first_name', ''),
                            "message": message.text or ""
                        })
                    )
                    session.add(event_obj)
                    await session.commit()

                    logger.info(f"REGA: Новый чат {chat.id} от {sender.id}")

                    # Отправляем приветственные сообщения от имени ДМ
                    await send_welcome_messages(session, new_chat, client, account.user_id)

                    # Уведомляем админа в бота
                    await notify_admin_new_chat(session, new_chat, sender)

                else:
                    # Существующий чат - увеличиваем счетчик
                    existing_chat.messages_count += 1
                    existing_chat.updated_at = func.now()
                    await session.commit()

                    # Проверяем на ДЕП (4-е сообщение от клиента)
                    if existing_chat.messages_count == 4 and existing_chat.status == ChatStatus.ACTIVE:
                        await process_dep_event(session, existing_chat, message, sender)

        except Exception as e:
            logger.error(f"Error in chat listener: {e}")

async def send_welcome_messages(session, chat, client, dm_user_id):
    """Отправляем приветственные сообщения от имени ДМ"""
    result = await session.execute(select(DMProfile).where(DMProfile.user_id == dm_user_id))
    dm_profile = result.scalar_one_or_none()

    if not dm_profile or not dm_profile.message1:
        logger.warning(f"DM профиль не заполнен для пользователя {dm_user_id}")
        return

    tag = dm_profile.tag or "Менеджер"

    try:
        # Первое сообщение
        msg1 = dm_profile.message1.replace("{tag}", tag)
        await client.send_message(chat.chat_id, msg1)

        # Второе сообщение (с небольшой задержкой)
        if dm_profile.message2:
            import asyncio
            await asyncio.sleep(1)
            msg2 = dm_profile.message2.replace("{tag}", tag)
            await client.send_message(chat.chat_id, msg2)

        logger.info(f"Отправлены приветственные сообщения в чат {chat.chat_id}")

    except Exception as e:
        logger.error(f"Ошибка отправки приветственных сообщений: {e}")

async def process_dep_event(session, chat, message, sender):
    """Фиксируем событие ДЕП"""
    event_data = {
        "user_id": sender.id,
        "username": getattr(sender, 'username', ''),
        "first_name": getattr(sender, 'first_name', ''),
        "chat_id": chat.chat_id,
        "chat_title": chat.chat_title,
        "message_text": message.text or "",
        "message_date": str(message.date) if hasattr(message, 'date') else ""
    }

    event_obj = Event(
        chat_id=chat.id,
        event_type=EventType.DEP,
        event_data=json.dumps(event_data)
    )
    session.add(event_obj)

    chat.status = ChatStatus.DEP_DONE
    chat.dep_data = json.dumps(event_data)
    await session.commit()

    logger.info(f"DEP: Зафиксировано в чате {chat.chat_id}")

    # Уведомляем админа
    await notify_admin_dep_event(session, chat, event_data)

async def notify_admin_new_chat(session, chat, sender):
    """Уведомляем админов о новом чате"""
    result = await session.execute(select(User).where(User.status == UserStatus.APPROVED))
    admins = result.scalars().all()

    text = f"""
📩 Новый чат (РЕГА)

👤 Клиент: {getattr(sender, 'first_name', '')} @{getattr(sender, 'username', '')}
ID: {sender.id}
💬 Чат: {chat.chat_title}
"""
    for admin in admins:
        try:
            await types.BotCommand()  # placeholder
            from bot.config import ADMIN_IDS
            if admin.tg_id in ADMIN_IDS:
                # Отправляем уведомление через бота
                pass
        except:
            pass

async def notify_admin_dep_event(session, chat, data):
    """Уведомляем админов о ДЕП событии"""
    from bot.config import ADMIN_IDS

    text = f"""
💰 ДЕП зафиксирован!

👤 Клиент: {data.get('first_name', '')} @{data.get('username', '')}
💬 Чат: {chat.chat_title}
Сообщение: {data.get('message_text', '')[:100]}
"""
    # Логирование для просмотра в админке
    logger.info(f"DEP notification: {text}")
