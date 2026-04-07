from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy import delete, func, select

from bot.db import (
    AccessRequest,
    BuyerDM,
    Chat,
    DMProfile,
    Event,
    EventType,
    RequestStatus,
    TGAccount,
    User,
    UserRole,
    UserStatus,
    async_session_maker,
)
from bot.keyboards import (
    get_admin_keyboard,
    get_approve_role_keyboard,
    get_back_keyboard,
    get_buyer_dm_select_keyboard,
    get_buyer_keyboard,
    get_buyer_profile_keyboard,
    get_dm_keyboard,
    get_dm_phrases_admin_keyboard,
    get_dm_profile_keyboard,
    get_dms_list_keyboard,
    get_profile_keyboard,
)

import logging

logger = logging.getLogger(__name__)
router = Router()


def is_admin_user(tg_id: int) -> bool:
    from bot.config import ADMIN_IDS
    return tg_id in ADMIN_IDS


async def get_pending_requests_count(session) -> int:
    result = await session.execute(select(func.count(AccessRequest.id)).where(AccessRequest.status == RequestStatus.PENDING))
    return result.scalar() or 0


async def get_available_dms(session) -> list[User]:
    from bot.config import ADMIN_IDS

    result = await session.execute(
        select(User)
        .where(User.status == UserStatus.APPROVED)
        .where(User.tg_id.not_in(ADMIN_IDS))
        .where(User.role == UserRole.DM.value)
        .order_by(User.username.asc(), User.first_name.asc(), User.tg_id.asc())
    )
    return list(result.scalars().all())


async def get_buyer_assigned_dm_tg_ids(session, buyer_user_id: int) -> list[int]:
    result = await session.execute(
        select(User.tg_id)
        .join(BuyerDM, BuyerDM.dm_user_id == User.id)
        .where(BuyerDM.buyer_user_id == buyer_user_id)
        .order_by(User.tg_id.asc())
    )
    return [row[0] for row in result.all()]


async def render_admin_user_profile(message, user: User) -> None:
    async with async_session_maker() as session:
        dm_result = await session.execute(select(DMProfile).where(DMProfile.user_id == user.id))
        dm_profile = dm_result.scalar_one_or_none()

        tg_result = await session.execute(select(TGAccount).where(TGAccount.user_id == user.id))
        tg_account = tg_result.scalar_one_or_none()

        if (user.role or UserRole.DM.value) == UserRole.BUYER.value:
            buyer_links_result = await session.execute(
                select(User)
                .join(BuyerDM, BuyerDM.dm_user_id == User.id)
                .where(BuyerDM.buyer_user_id == user.id)
                .order_by(User.username.asc(), User.tg_id.asc())
            )
            assigned_dms = buyer_links_result.scalars().all()
            assigned_text = "\n".join(
                [f"• @{item.username}" if item.username else f"• {item.tg_id}" for item in assigned_dms]
            ) or "Нет назначенных DM"
            text = (
                "Профиль Buyer\n\n"
                f"TG ID: {user.tg_id}\n"
                f"Username: @{user.username or 'N/A'}\n"
                f"Роль: {user.role}\n"
                f"Назначенные DM:\n{assigned_text}"
            )
            await message.edit_text(text, reply_markup=get_buyer_profile_keyboard(user.tg_id))
            return

        text = (
            "Профиль DM\n\n"
            f"TG ID: {user.tg_id}\n"
            f"Username: @{user.username or 'N/A'}\n"
            f"Тег: {dm_profile.tag if dm_profile and dm_profile.tag else 'Не задан'}\n"
            f"TG аккаунт: {'Подключен' if tg_account else 'Не подключен'}\n"
        )

        if dm_profile:
            if dm_profile.greeting_message1:
                text += f"\nПриветствие 1: {dm_profile.greeting_message1[:50]}..."
            if dm_profile.greeting_message2:
                text += f"\nПриветствие 2: {dm_profile.greeting_message2[:50]}..."
            if dm_profile.dodep_keyword:
                text += f"\nКлючевая фраза: {dm_profile.dodep_keyword}"

        await message.edit_text(text, reply_markup=get_dm_profile_keyboard(bool(tg_account)))


async def finalize_user_approval(
    callback: CallbackQuery,
    user_tg_id: int,
    role: str,
    dm_tg_ids: list[int] | None = None,
) -> bool:
    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.tg_id == user_tg_id))
        user = result.scalar_one_or_none()
        if not user:
            return False

        req_result = await session.execute(select(AccessRequest).where(AccessRequest.user_id == user.id))
        request = req_result.scalar_one_or_none()
        if not request:
            return False

        request.status = RequestStatus.APPROVED
        request.processed_at = func.now()
        user.status = UserStatus.APPROVED
        user.role = role

        await session.execute(delete(BuyerDM).where(BuyerDM.buyer_user_id == user.id))
        if role == UserRole.BUYER.value:
            dm_tg_ids = dm_tg_ids or []
            dm_result = await session.execute(select(User).where(User.tg_id.in_(dm_tg_ids)))
            dm_users = list(dm_result.scalars().all())
            for dm_user in dm_users:
                session.add(BuyerDM(buyer_user_id=user.id, dm_user_id=dm_user.id))

        await session.commit()

        try:
            if role == UserRole.BUYER.value:
                await callback.bot.send_message(user.tg_id, "Ваш доступ одобрен. Роль: Buyer. Используйте /start")
            else:
                await callback.bot.send_message(user.tg_id, "Ваш доступ одобрен. Роль: DM. Используйте /start")
        except Exception:
            logger.exception("Failed to notify approved user %s", user.tg_id)

    return True


@router.callback_query(F.data == "admin_requests")
async def admin_requests_handler(callback: CallbackQuery):
    async with async_session_maker() as session:
        from bot.config import ADMIN_IDS

        result = await session.execute(
            select(AccessRequest, User)
            .join(User, AccessRequest.user_id == User.id)
            .where(AccessRequest.status == RequestStatus.PENDING)
            .where(User.tg_id.not_in(ADMIN_IDS))
            .order_by(AccessRequest.created_at.asc())
        )
        requests = result.all()

        count = await get_pending_requests_count(session)

    if not requests:
        await callback.message.edit_text("Нет новых запросов", reply_markup=get_admin_keyboard())
        await callback.answer()
        return

    lines = [f"Заявки на подтверждение: {count}", ""]
    for request, user in requests:
        username = f"@{user.username}" if user.username else "N/A"
        lines.append(f"User: {user.tg_id} ({username})")

    await callback.message.edit_text("\n".join(lines), reply_markup=get_admin_keyboard(count))
    await callback.answer()


@router.callback_query(F.data.regexp(r"^approve_\d+$"))
async def approve_handler(callback: CallbackQuery):
    user_id = int(callback.data.split("_")[1])
    await callback.message.edit_text(
        f"Выберите роль для пользователя {user_id}:",
        reply_markup=get_approve_role_keyboard(user_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("approve_role_dm_"))
async def approve_role_dm_handler(callback: CallbackQuery):
    user_id = int(callback.data.split("_")[-1])
    ok = await finalize_user_approval(callback, user_id, UserRole.DM.value)
    if ok:
        await callback.message.edit_text(f"Пользователь {user_id} одобрен как DM.")
    else:
        await callback.message.edit_text(f"Не удалось одобрить пользователя {user_id}.")
    await callback.answer()


@router.callback_query(F.data.startswith("approve_role_buyer_"))
async def approve_role_buyer_handler(callback: CallbackQuery, state: FSMContext):
    user_id = int(callback.data.split("_")[-1])

    async with async_session_maker() as session:
        dms = await get_available_dms(session)

    if not dms:
        await callback.answer("Нет доступных DM для назначения", show_alert=True)
        return

    await state.update_data(buyer_user_id=user_id, selected_buyer_dm_ids=[])
    dms_payload = [{"tg_id": dm.tg_id, "username": dm.username, "first_name": dm.first_name} for dm in dms]
    await callback.message.edit_text(
        "Выберите одного или нескольких DM для этого buyer:",
        reply_markup=get_buyer_dm_select_keyboard(user_id, dms_payload, set()),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("buyer_toggle_dm_"))
async def buyer_toggle_dm_handler(callback: CallbackQuery, state: FSMContext):
    _, _, _, user_id_str, dm_tg_id_str = callback.data.split("_")
    user_id = int(user_id_str)
    dm_tg_id = int(dm_tg_id_str)

    data = await state.get_data()
    selected = set(data.get("selected_buyer_dm_ids", []))
    if dm_tg_id in selected:
        selected.remove(dm_tg_id)
    else:
        selected.add(dm_tg_id)

    await state.update_data(buyer_user_id=user_id, selected_buyer_dm_ids=sorted(selected))

    async with async_session_maker() as session:
        dms = await get_available_dms(session)

    dms_payload = [{"tg_id": dm.tg_id, "username": dm.username, "first_name": dm.first_name} for dm in dms]
    await callback.message.edit_reply_markup(
        reply_markup=get_buyer_dm_select_keyboard(user_id, dms_payload, selected)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("buyer_confirm_"))
async def buyer_confirm_handler(callback: CallbackQuery, state: FSMContext):
    user_id = int(callback.data.split("_")[-1])
    data = await state.get_data()
    selected_dm_ids = data.get("selected_buyer_dm_ids", [])

    if not selected_dm_ids:
        await callback.answer("Выберите хотя бы одного DM", show_alert=True)
        return

    ok = await finalize_user_approval(callback, user_id, UserRole.BUYER.value, selected_dm_ids)
    await state.clear()

    if ok:
        selected_text = ", ".join(str(item) for item in selected_dm_ids)
        await callback.message.edit_text(
            f"Пользователь {user_id} одобрен как Buyer.\nНазначенные DM: {selected_text}"
        )
    else:
        await callback.message.edit_text(f"Не удалось одобрить пользователя {user_id}.")
    await callback.answer()


@router.callback_query(F.data.regexp(r"^reject_\d+$"))
async def reject_handler(callback: CallbackQuery, state: FSMContext):
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
                await callback.message.edit_text(f"Пользователь {user_id} отклонен.")

    data = await state.get_data()
    if data.get("buyer_user_id") == user_id:
        await state.clear()
    await callback.answer()


@router.callback_query(F.data == "admin_stats")
async def admin_stats_handler(callback: CallbackQuery):
    async with async_session_maker() as session:
        total_users_result = await session.execute(select(func.count(User.id)).where(User.status == UserStatus.APPROVED))
        total_users = total_users_result.scalar() or 0

        dm_users_result = await session.execute(
            select(func.count(User.id)).where(User.status == UserStatus.APPROVED).where(User.role == UserRole.DM.value)
        )
        dm_users = dm_users_result.scalar() or 0

        buyer_users_result = await session.execute(
            select(func.count(User.id)).where(User.status == UserStatus.APPROVED).where(User.role == UserRole.BUYER.value)
        )
        buyer_users = buyer_users_result.scalar() or 0

        total_chats_result = await session.execute(select(func.count(Chat.id)))
        total_chats = total_chats_result.scalar() or 0

        rega_result = await session.execute(select(func.count(Event.id)).where(Event.event_type == EventType.REGA))
        rega = rega_result.scalar() or 0

        dep_result = await session.execute(select(func.count(Event.id)).where(Event.event_type == EventType.DEP))
        dep = dep_result.scalar() or 0

        do_dep_result = await session.execute(select(func.count(Event.id)).where(Event.event_type == EventType.DO_DEP))
        do_dep = do_dep_result.scalar() or 0

    text = (
        "Статистика:\n\n"
        f"Одобренных пользователей: {total_users}\n"
        f"DM: {dm_users}\n"
        f"Buyer: {buyer_users}\n"
        f"Чатов: {total_chats}\n\n"
        f"Реги: {rega}\n"
        f"Депы: {dep}\n"
        f"До депы: {do_dep}"
    )
    await callback.message.edit_text(text, reply_markup=get_back_keyboard())
    await callback.answer()


@router.callback_query(F.data == "admin_dms")
async def admin_dms_handler(callback: CallbackQuery):
    async with async_session_maker() as session:
        from bot.config import ADMIN_IDS
        result = await session.execute(
            select(User)
            .where(User.status == UserStatus.APPROVED)
            .where(User.tg_id.not_in(ADMIN_IDS))
            .order_by(User.role.asc(), User.username.asc(), User.first_name.asc(), User.tg_id.asc())
        )
        users = list(result.scalars().all())

    if not users:
        await callback.message.edit_text("Нет пользователей", reply_markup=get_admin_keyboard())
        await callback.answer()
        return

    dms_list = [{"tg_id": item.tg_id, "username": item.username, "first_name": item.first_name, "role": item.role} for item in users]
    await callback.message.edit_text(
        f"Пользователи ({len(dms_list)}):\n\n"
        + "\n".join(
            [
                f"{'🛒' if d['role'] == UserRole.BUYER.value else '👤'} {d['username'] or d['tg_id']} ({d['role']})"
                for d in dms_list
            ]
        ),
        reply_markup=get_dms_list_keyboard(dms_list),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("dm_profile_"))
async def dm_profile_handler(callback: CallbackQuery, state: FSMContext):
    tg_id = int(callback.data.split("_")[2])
    await state.update_data(target_dm_tg_id=tg_id)

    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalar_one_or_none()
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return
    await render_admin_user_profile(callback.message, user)

    await callback.answer()


@router.callback_query(F.data.startswith("edit_buyer_dms_"))
async def edit_buyer_dms_handler(callback: CallbackQuery, state: FSMContext):
    buyer_tg_id = int(callback.data.split("_")[-1])

    async with async_session_maker() as session:
        buyer_result = await session.execute(select(User).where(User.tg_id == buyer_tg_id))
        buyer = buyer_result.scalar_one_or_none()
        if not buyer:
            await callback.answer("Buyer не найден", show_alert=True)
            return

        dms = await get_available_dms(session)
        selected_dm_ids = set(await get_buyer_assigned_dm_tg_ids(session, buyer.id))

    await state.update_data(buyer_user_id=buyer_tg_id, selected_buyer_dm_ids=sorted(selected_dm_ids), buyer_edit_mode=True)
    dms_payload = [{"tg_id": dm.tg_id, "username": dm.username, "first_name": dm.first_name} for dm in dms]
    await callback.message.edit_text(
        f"Редактирование DM для buyer {buyer_tg_id}:",
        reply_markup=get_buyer_dm_select_keyboard(
            buyer_tg_id,
            dms_payload,
            selected_dm_ids,
            confirm_callback=f"buyer_save_edit_{buyer_tg_id}",
            back_callback=f"dm_profile_{buyer_tg_id}",
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("buyer_save_edit_"))
async def buyer_save_edit_handler(callback: CallbackQuery, state: FSMContext):
    buyer_tg_id = int(callback.data.split("_")[-1])
    data = await state.get_data()
    selected_dm_ids = data.get("selected_buyer_dm_ids", [])

    async with async_session_maker() as session:
        buyer_result = await session.execute(select(User).where(User.tg_id == buyer_tg_id))
        buyer = buyer_result.scalar_one_or_none()
        if not buyer:
            await callback.answer("Buyer не найден", show_alert=True)
            return

        await session.execute(delete(BuyerDM).where(BuyerDM.buyer_user_id == buyer.id))
        if selected_dm_ids:
            dm_result = await session.execute(select(User).where(User.tg_id.in_(selected_dm_ids)))
            for dm_user in dm_result.scalars().all():
                session.add(BuyerDM(buyer_user_id=buyer.id, dm_user_id=dm_user.id))
        await session.commit()

    await state.clear()
    await render_admin_user_profile(callback.message, buyer)
    await callback.answer("Привязки buyer обновлены")


@router.callback_query(F.data == "admin_back")
async def admin_back_handler(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    async with async_session_maker() as session:
        count = await get_pending_requests_count(session)

    await callback.message.edit_text("Меню администратора:", reply_markup=get_admin_keyboard(count))
    await callback.answer()


@router.callback_query(F.data == "my_profile")
async def my_profile_handler(callback: CallbackQuery):
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
            text = (
                "Профиль администратора\n"
                f"TG ID: {user.tg_id}\n"
                f"Username: @{user.username or 'N/A'}\n"
            )
            await callback.message.edit_text(text, reply_markup=get_profile_keyboard(bool(dm_profile), is_admin=True))
        elif (user.role or UserRole.DM.value) == UserRole.BUYER.value:
            buyer_links_result = await session.execute(
                select(User)
                .join(BuyerDM, BuyerDM.dm_user_id == User.id)
                .where(BuyerDM.buyer_user_id == user.id)
                .order_by(User.username.asc(), User.tg_id.asc())
            )
            assigned_dms = buyer_links_result.scalars().all()
            assigned_text = ", ".join(f"@{item.username}" if item.username else str(item.tg_id) for item in assigned_dms) or "Нет"
            text = (
                "Профиль buyer\n"
                f"Статус: {user.status.value}\n"
                f"Назначенные DM: {assigned_text}\n"
            )
            await callback.message.edit_text(text, reply_markup=get_buyer_keyboard())
        else:
            text = (
                "Ваш профиль DM\n"
                f"Статус: {user.status.value}\n"
                f"TG аккаунт: {'Подключен' if tg_account else 'Не подключен'}\n"
            )
            if dm_profile:
                if dm_profile.tag:
                    text += f"Тег: {dm_profile.tag}\n"
                if dm_profile.greeting_message1:
                    text += "Приветствие 1: настроено\n"
                if dm_profile.greeting_message2:
                    text += "Приветствие 2: настроено\n"
                if dm_profile.dodep_keyword:
                    text += "Ключевая фраза: настроена\n"

            has_profile = bool(dm_profile and dm_profile.tag)
            await callback.message.edit_text(text, reply_markup=get_dm_keyboard(bool(tg_account), has_profile))

    await callback.answer()


@router.callback_query(F.data == "dm_edit_phrases_admin")
async def dm_edit_phrases_admin_handler(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    target_dm_tg_id = data.get("target_dm_tg_id")
    if not target_dm_tg_id:
        await callback.answer("Не выбран пользователь", show_alert=True)
        return

    await callback.message.edit_text(
        "Настройка фраз трекинга\n\n"
        "Здесь вы можете настроить сообщения для трекинга событий:\n"
        "1. Два сообщения после приветствия\n"
        "2. Ключевую фразу для DO_DEP",
        reply_markup=get_dm_phrases_admin_keyboard(),
    )
    await callback.answer()
