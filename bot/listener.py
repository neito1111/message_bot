"""Telegram listener for DM account tracking."""
from telethon import events
from sqlalchemy import select, func
from bot.db import async_session_maker, Chat, Event, EventType, ChatStatus, User, DMProfile, EventSequence
import logging
import json

logger = logging.getLogger(__name__)

REGA_TRIGGER = "\u0432\u0456\u0442\u0430\u044e"
DEFAULT_DODEP_KEYWORDS = [
    "\u042f\u043a \u043e\u0442\u0440\u0438\u043c\u0430\u0454\u0442\u0435 \u0432\u0438\u043f\u043b\u0430\u0442\u0443",
    "\u042f\u043a \u043e\u0442\u0440\u0438\u043c\u0430\u0454\u0442\u0435 \u0432\u0438\u043f\u043b\u0430\u0442\u0443, \u0431\u0443\u0434\u044c \u043b\u0430\u0441\u043a\u0430 \u0432\u0456\u0434\u043f\u0440\u0430\u0432\u0442\u0435 \u0441\u043a\u0440\u0456\u043d \u0449\u043e \u043a\u043e\u0448\u0442\u0438 \u043f\u0440\u0438\u0439\u0448\u043b\u0438\n\u0422\u0430 \u044f\u043a\u0438\u0439\u0441\u044c \u0441\u043a\u0440\u043e\u043c\u043d\u0438\u0439 \u0432\u0456\u0434\u0433\u0443\u043a \u0432\u0456\u0434 \u0432\u0430\u0441",
]


def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    normalized = text.lower()
    normalized = normalized.replace("i", "\u0456").replace("\u00ef", "\u0456")
    normalized = normalized.replace("\u2019", "'").replace("`", "'")
    normalized = normalized.replace("\u0451", "\u0435")
    return " ".join(normalized.split())


def get_dodep_keywords(dm_profile: DMProfile | None) -> list[str]:
    keywords: list[str] = []
    if dm_profile and dm_profile.dodep_keyword:
        keywords.append(dm_profile.dodep_keyword)
    keywords.extend(DEFAULT_DODEP_KEYWORDS)
    return [normalize_text(keyword) for keyword in keywords if keyword]


def get_dodep_prefixes(dm_profile: DMProfile | None, words_count: int = 3) -> list[str]:
    prefixes: list[str] = []
    for keyword in get_dodep_keywords(dm_profile):
        words = keyword.split()
        if not words:
            continue
        prefixes.append(" ".join(words[:words_count]))
    return list(dict.fromkeys(prefixes))


def setup_message_listener(client, account):
    @client.on(events.NewMessage(incoming=True))
    async def incoming_handler(event):
        try:
            await process_incoming_message(event, account)
        except Exception as exc:
            logger.error("Error in incoming_handler: %s", exc)

    @client.on(events.NewMessage(outgoing=True))
    async def outgoing_handler(event):
        try:
            await process_outgoing_message(event, account)
        except Exception as exc:
            logger.error("Error in outgoing_handler: %s", exc)


async def create_or_reset_rega(session, chat_obj: Chat, account, chat, sender, message) -> None:
    text = message.text or ""
    chat_obj.chat_title = getattr(chat, "title", None) or getattr(chat, "first_name", "Unknown")
    chat_obj.user_first_name = getattr(sender, "first_name", "")
    chat_obj.user_username = getattr(sender, "username", None)
    chat_obj.user_tg_id = sender.id
    chat_obj.messages_count = 1
    chat_obj.first_message_text = text
    chat_obj.status = ChatStatus.ACTIVE
    chat_obj.dep_data = None
    chat_obj.updated_at = func.now()
    session.add(chat_obj)
    await session.flush()

    event_data = {
        "user_id": sender.id,
        "username": getattr(sender, "username", ""),
        "first_name": getattr(sender, "first_name", ""),
        "message": text,
    }
    session.add(Event(chat_id=chat_obj.id, event_type=EventType.REGA, event_data=json.dumps(event_data, ensure_ascii=False)))
    session.add(EventSequence(
        tg_account_id=account.id,
        chat_id=chat_obj.id,
        user_tg_id=sender.id,
        rega_done=1,
        dep_done=0,
        dodep_done=0,
        rega_message_id=message.id,
        dep_messages_count=0,
        user_messages_after_rega=1,
    ))
    await session.commit()
    await send_welcome_messages(session, chat_obj, account)


async def process_incoming_message(event, account):
    chat = await event.get_chat()
    sender = await event.get_sender()
    message = event.message

    if not chat or not sender:
        return

    if getattr(sender, "bot", False):
        return

    if getattr(sender, "id", None) == account.user_id:
        return

    async with async_session_maker() as session:
        result = await session.execute(
            select(Chat).where(
                Chat.tg_account_id == account.id,
                Chat.chat_id == chat.id,
            )
        )
        existing_chat = result.scalars().first()
        text = message.text or ""
        normalized_text = normalize_text(text)

        if not existing_chat:
            if REGA_TRIGGER in normalized_text:
                logger.info("REGA: New chat %s from user %s", chat.id, sender.id)
                new_chat = Chat(
                    tg_account_id=account.id,
                    chat_id=chat.id,
                    chat_title=getattr(chat, "title", None) or getattr(chat, "first_name", "Unknown"),
                    user_first_name=getattr(sender, "first_name", ""),
                    user_username=getattr(sender, "username", None),
                    user_tg_id=sender.id,
                )
                await create_or_reset_rega(session, new_chat, account, chat, sender, message)
            else:
                logger.info("New chat %s but no REGA trigger in message", chat.id)
            return

        seq_result = await session.execute(select(EventSequence).where(EventSequence.chat_id == existing_chat.id))
        event_seq = seq_result.scalars().first()

        if not event_seq:
            if REGA_TRIGGER in normalized_text:
                logger.info("REGA: Reinitializing orphaned chat %s for user %s", chat.id, sender.id)
                await create_or_reset_rega(session, existing_chat, account, chat, sender, message)
            else:
                existing_chat.messages_count += 1
                existing_chat.updated_at = func.now()
                await session.commit()
            return

        existing_chat.messages_count += 1
        existing_chat.updated_at = func.now()
        event_seq.user_messages_after_rega += 1

        if (
            not event_seq.dep_done
            and existing_chat.status == ChatStatus.ACTIVE
            and event_seq.dep_messages_count >= 2
            and event_seq.user_messages_after_rega >= 2
        ):
            logger.info("DEP: first incoming after welcome sequence in chat %s", chat.id)
            await process_dep_event(session, existing_chat, event_seq, message, sender)

        await session.commit()


async def process_outgoing_message(event, account):
    chat = await event.get_chat()
    message = event.message
    text = message.text or ""

    if not chat:
        return

    async with async_session_maker() as session:
        result = await session.execute(
            select(Chat).where(
                Chat.tg_account_id == account.id,
                Chat.chat_id == chat.id,
            )
        )
        chat_obj = result.scalars().first()
        if not chat_obj:
            return

        chat_obj.updated_at = func.now()

        seq_result = await session.execute(select(EventSequence).where(EventSequence.chat_id == chat_obj.id))
        event_seq = seq_result.scalars().first()

        if event_seq and event_seq.dep_done and not event_seq.dodep_done:
            dm_result = await session.execute(select(DMProfile).where(DMProfile.user_id == account.user_id))
            dm_profile = dm_result.scalars().first()
            normalized_text = normalize_text(text)
            matched_prefix = next(
                (prefix for prefix in get_dodep_prefixes(dm_profile) if prefix and normalized_text.startswith(prefix)),
                None,
            )
            if matched_prefix:
                logger.info("DO_DEP: Prefix '%s' matched at start of message in chat %s", matched_prefix, chat.id)
                event_data = {
                    "trigger_message": text,
                    "message_id": message.id,
                    "keyword_prefix": matched_prefix,
                }
                session.add(Event(chat_id=chat_obj.id, event_type=EventType.DO_DEP, event_data=json.dumps(event_data, ensure_ascii=False)))
                event_seq.dodep_done = 1
                chat_obj.status = ChatStatus.DO_DEP_DONE

        await session.commit()


async def send_welcome_messages(session, chat: Chat, account):
    result = await session.execute(select(User).where(User.id == account.user_id))
    user = result.scalar_one_or_none()
    if not user:
        return

    dm_result = await session.execute(select(DMProfile).where(DMProfile.user_id == user.id))
    dm_profile = dm_result.scalar_one_or_none()
    if not dm_profile:
        logger.warning("DM profile not found for user %s", user.id)
        return

    msg1_text = dm_profile.greeting_message1 or dm_profile.message1
    msg2_text = dm_profile.greeting_message2 or dm_profile.message2
    if not msg1_text:
        logger.warning("No greeting messages configured for DM %s", user.id)
        return

    tag = dm_profile.tag or "manager"

    try:
        from bot.client import tg_manager

        client = tg_manager.get_client(account.user_id)
        if not client:
            return
        sent_count = 0
        await client.send_message(chat.chat_id, msg1_text.replace("{tag}", tag))
        sent_count += 1
        logger.info("Sent greeting message 1 to chat %s", chat.chat_id)
        if msg2_text:
            import asyncio
            await asyncio.sleep(1)
            await client.send_message(chat.chat_id, msg2_text.replace("{tag}", tag))
            sent_count += 1
            logger.info("Sent greeting message 2 to chat %s", chat.chat_id)

        if sent_count:
            chat.messages_count = max(chat.messages_count or 0, 1 + sent_count)
            chat.updated_at = func.now()
            seq_result = await session.execute(select(EventSequence).where(EventSequence.chat_id == chat.id))
            event_seq = seq_result.scalars().first()
            if event_seq:
                event_seq.dep_messages_count = sent_count
            await session.commit()
    except Exception as exc:
        logger.error("Error sending welcome messages: %s", exc)


async def process_dep_event(session, chat: Chat, event_seq: EventSequence, message, sender):
    event_data = {
        "user_id": sender.id,
        "username": getattr(sender, "username", ""),
        "first_name": getattr(sender, "first_name", ""),
        "chat_id": chat.chat_id,
        "chat_title": chat.chat_title,
        "message_text": message.text or "",
        "message_date": str(getattr(message, "date", "")),
        "user_messages_count": event_seq.user_messages_after_rega,
    }
    session.add(Event(chat_id=chat.id, event_type=EventType.DEP, event_data=json.dumps(event_data, ensure_ascii=False)))
    chat.status = ChatStatus.DEP_DONE
    chat.dep_data = json.dumps(event_data, ensure_ascii=False)
    event_seq.dep_done = 1
    logger.info("DEP event logged for chat %s", chat.id)
