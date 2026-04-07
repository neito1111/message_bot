from collections import defaultdict
from datetime import datetime, timedelta, timezone
import asyncio
import json
import logging
import os
import tempfile

from aiogram import F, Router
from aiogram.types import CallbackQuery, FSInputFile
from sqlalchemy import select

from bot.config import ADMIN_IDS
from bot.db import BuyerDM, Chat, DMProfile, Event, EventType, TGAccount, User, UserRole, async_session_maker

logger = logging.getLogger(__name__)
router = Router()
UKRAINE_FIXED_TZ = timezone(timedelta(hours=2))

LOADING_EMOJIS = [
    "\U0001F311",
    "\U0001F312",
    "\U0001F313",
    "\U0001F314",
    "\U0001F315",
    "\U0001F316",
    "\U0001F317",
    "\U0001F318",
]


def is_admin_user(tg_id: int) -> bool:
    return tg_id in ADMIN_IDS


async def get_allowed_dm_user_ids(session, user: User) -> list[int] | None:
    if is_admin_user(user.tg_id):
        return None

    if (user.role or UserRole.DM.value) == UserRole.BUYER.value:
        result = await session.execute(select(BuyerDM.dm_user_id).where(BuyerDM.buyer_user_id == user.id))
        return [row[0] for row in result.all()]

    return [user.id]


async def get_requester(session, tg_id: int) -> User | None:
    result = await session.execute(select(User).where(User.tg_id == tg_id))
    return result.scalar_one_or_none()


def parse_event_data(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw}


def format_ukraine_time(dt) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(UKRAINE_FIXED_TZ).strftime("%Y-%m-%d %H:%M:%S")


def sanitize_sheet_title(title: str, fallback: str) -> str:
    cleaned = "".join("_" if ch in '[]:*?/\\' else ch for ch in (title or "")).strip()
    cleaned = cleaned[:31].strip()
    return cleaned or fallback


def append_rows_with_day_separators(ws, headers: list[str], rows: list[dict]):
    ws.append(headers)
    current_day = None

    for row in rows:
        row_day = row.pop("_day", None)
        if row_day and row_day != current_day:
            separator_row_index = ws.max_row + 1
            ws.append([str(row_day)] + [""] * (len(headers) - 1))
            ws.merge_cells(start_row=separator_row_index, start_column=1, end_row=separator_row_index, end_column=len(headers))
            separator_cell = ws.cell(row=separator_row_index, column=1)
            separator_cell.fill = separator_cell.fill.copy(fill_type="solid", fgColor="000000")
            separator_cell.font = separator_cell.font.copy(bold=True, color="FFFFFF")
            separator_cell.alignment = separator_cell.alignment.copy(horizontal="center")
            current_day = row_day

        ws.append([row.get(header, "") for header in headers])


async def generate_stats_summary(dm_user_ids: list[int] | None) -> dict:
    async with async_session_maker() as session:
        summary_stmt = (
            select(Chat, TGAccount, User, DMProfile, Event)
            .join(TGAccount, Chat.tg_account_id == TGAccount.id)
            .join(User, TGAccount.user_id == User.id)
            .outerjoin(DMProfile, DMProfile.user_id == User.id)
            .outerjoin(Event, Event.chat_id == Chat.id)
            .order_by(Chat.created_at.desc(), Chat.id.desc(), Event.created_at.asc(), Event.id.asc())
        )
        if dm_user_ids is not None:
            if not dm_user_ids:
                return {"summary_data": [], "manager_sheets": {}}
            summary_stmt = summary_stmt.where(User.id.in_(dm_user_ids))

        summary_result = await session.execute(summary_stmt)
        summary_rows = summary_result.all()

    summary_by_chat: dict[int, dict] = {}
    for chat, tg_account, manager_user, dm_profile, event in summary_rows:
        item = summary_by_chat.get(chat.id)
        if item is None:
            manager_username = f"@{manager_user.username}" if manager_user and manager_user.username else "N/A"
            lead_username = chat.user_username or "N/A"
            if lead_username != "N/A" and not str(lead_username).startswith("@"):
                lead_username = f"@{lead_username}"

            item = {
                "Lead TG ID": chat.user_tg_id or "N/A",
                "Lead Username": lead_username,
                "Lead Name": chat.user_first_name or "N/A",
                "Manager TG ID": manager_user.tg_id if manager_user else "N/A",
                "Manager Username": manager_username,
                "Manager Tag": dm_profile.tag if dm_profile and dm_profile.tag else "N/A",
                "Chat Title": chat.chat_title or "N/A",
                "Messages Total": chat.messages_count or 0,
                "REGA Time": "",
                "REGA": chat.first_message_text or "",
                "DEP Time": "",
                "DEP": "",
                "DO_DEP Time": "",
                "DO_DEP": "",
            }
            summary_by_chat[chat.id] = item

        if event and event.event_type:
            event_data = parse_event_data(event.event_data)
            event_time = format_ukraine_time(event.created_at)
            if event.event_type == EventType.REGA:
                item["REGA Time"] = event_time
                item["REGA"] = event_data.get("message") or chat.first_message_text or ""
            elif event.event_type == EventType.DEP:
                item["DEP Time"] = event_time
                item["DEP"] = event_data.get("message_text") or ""
            elif event.event_type == EventType.DO_DEP:
                item["DO_DEP Time"] = event_time
                item["DO_DEP"] = event_data.get("trigger_message") or ""

    summary_data = [dict(item) for item in summary_by_chat.values() if any((item["REGA Time"], item["DEP Time"], item["DO_DEP Time"]))]

    def effective_dt(row: dict):
        for key in ("DO_DEP Time", "DEP Time", "REGA Time"):
            value = row.get(key)
            if value:
                return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        return datetime.min

    summary_data.sort(key=effective_dt)
    manager_sheets: dict[str, list[dict]] = defaultdict(list)
    for row in summary_data:
        row["_day"] = effective_dt(row).date() if effective_dt(row) != datetime.min else None
        manager_key = row["Manager Tag"] if row["Manager Tag"] != "N/A" else row["Manager Username"]
        manager_sheets[manager_key].append(dict(row))

    return {"summary_data": summary_data, "manager_sheets": dict(manager_sheets)}


async def generate_stats_overview(dm_user_ids: list[int] | None) -> dict:
    async with async_session_maker() as session:
        chat_stmt = (
            select(Chat)
            .join(TGAccount, Chat.tg_account_id == TGAccount.id)
            .join(User, TGAccount.user_id == User.id)
        )
        event_stmt = (
            select(Event)
            .join(Chat, Event.chat_id == Chat.id)
            .join(TGAccount, Chat.tg_account_id == TGAccount.id)
            .join(User, TGAccount.user_id == User.id)
        )

        if dm_user_ids is not None:
            if not dm_user_ids:
                return {"chats": 0, "messages": 0, "rega": 0, "dep": 0, "do_dep": 0}
            chat_stmt = chat_stmt.where(User.id.in_(dm_user_ids))
            event_stmt = event_stmt.where(User.id.in_(dm_user_ids))

        chats_result = await session.execute(chat_stmt)
        chats = chats_result.scalars().all()
        chat_count = len(chats)
        messages = sum(chat.messages_count or 0 for chat in chats)

        events_result = await session.execute(event_stmt)
        events = events_result.scalars().all()

    rega = sum(1 for event in events if event.event_type == EventType.REGA)
    dep = sum(1 for event in events if event.event_type == EventType.DEP)
    do_dep = sum(1 for event in events if event.event_type == EventType.DO_DEP)
    return {"chats": chat_count, "messages": messages, "rega": rega, "dep": dep, "do_dep": do_dep}


async def generate_stats_excel(dm_user_ids: list[int] | None, loading_message=None) -> str:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError:
        raise ImportError("openpyxl не установлен. Установите: pip install openpyxl")

    data = await generate_stats_summary(dm_user_ids)
    summary_data = data["summary_data"]
    manager_sheets = data["manager_sheets"]

    wb = Workbook()
    wb.remove(wb.active)

    summary_headers = [
        "Lead TG ID",
        "Lead Username",
        "Lead Name",
        "Manager TG ID",
        "Manager Username",
        "Manager Tag",
        "Chat Title",
        "Messages Total",
        "REGA Time",
        "REGA",
        "DEP Time",
        "DEP",
        "DO_DEP Time",
        "DO_DEP",
    ]

    sheets = [("Summary", summary_data)]
    for manager_key, manager_rows in sorted(manager_sheets.items(), key=lambda item: item[0]):
        sheets.append((sanitize_sheet_title(manager_key, "DM"), manager_rows))

    header_fill = PatternFill(fill_type="solid", fgColor="FFFF00")
    header_font = Font(bold=True)
    separator_fill = PatternFill(fill_type="solid", fgColor="000000")
    separator_font = Font(bold=True, color="FFFFFF")
    separator_alignment = Alignment(horizontal="center")

    frame = 0
    for sheet_name, rows in sheets:
        if loading_message:
            emoji = LOADING_EMOJIS[frame % len(LOADING_EMOJIS)]
            frame += 1
            try:
                await loading_message.edit_text(f"{emoji} Генерация листа: {sheet_name}...")
            except Exception:
                pass
            await asyncio.sleep(0.2)

        ws = wb.create_sheet(title=sheet_name)
        if rows:
            append_rows_with_day_separators(ws, summary_headers, [dict(row) for row in rows])
        else:
            ws.append(["No data"])

        for cell in ws[1]:
            cell.font = header_font
            cell.fill = header_fill

        for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
            if len(row) == 1 and row[0].value:
                row[0].fill = separator_fill
                row[0].font = separator_font
                row[0].alignment = separator_alignment

    if loading_message:
        try:
            await loading_message.edit_text("Готово. Отправляю файл...")
        except Exception:
            pass
        await asyncio.sleep(0.5)

    temp_fd, temp_path = tempfile.mkstemp(suffix=".xlsx")
    os.close(temp_fd)
    wb.save(temp_path)
    return temp_path


async def send_stats_excel(callback: CallbackQuery, dm_user_ids: list[int] | None, label: str) -> None:
    loading_message = await callback.message.answer("Генерация отчета...")
    await callback.answer()

    temp_file = None
    try:
        temp_file = await generate_stats_excel(dm_user_ids, loading_message)
        filename = f"{label}_{int(asyncio.get_event_loop().time())}.xlsx"
        input_file = FSInputFile(temp_file, filename=filename)
        await callback.message.answer_document(
            document=input_file,
            caption="Статистика выгружена",
        )
    except Exception as exc:
        logger.error("Error exporting stats: %s", exc)
        try:
            await loading_message.edit_text(f"Ошибка при генерации файла: {str(exc)[:200]}")
        except Exception:
            await callback.message.answer(f"Ошибка: {str(exc)[:200]}")
    finally:
        if temp_file and os.path.exists(temp_file):
            try:
                os.unlink(temp_file)
            except Exception:
                pass
        try:
            await loading_message.delete()
        except Exception:
            pass


@router.callback_query(F.data == "admin_export_stats")
async def admin_export_stats_handler(callback: CallbackQuery):
    async with async_session_maker() as session:
        user = await get_requester(session, callback.from_user.id)
        if not user or not is_admin_user(callback.from_user.id):
            await callback.answer("Недостаточно прав", show_alert=True)
            return

    await send_stats_excel(callback, None, "admin_stats")


@router.callback_query(F.data == "buyer_export_stats")
async def buyer_export_stats_handler(callback: CallbackQuery):
    async with async_session_maker() as session:
        user = await get_requester(session, callback.from_user.id)
        if not user:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        dm_user_ids = await get_allowed_dm_user_ids(session, user)
        if is_admin_user(callback.from_user.id) or (user.role or UserRole.DM.value) != UserRole.BUYER.value:
            await callback.answer("Недостаточно прав", show_alert=True)
            return

    await send_stats_excel(callback, dm_user_ids, "buyer_stats")
