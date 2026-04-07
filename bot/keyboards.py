from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

def get_start_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📝 Отправить заявку", callback_data="send_request")
    return builder.as_markup()

def get_admin_keyboard(requests_count: int = 0) -> InlineKeyboardMarkup:
    """Клавиатура администратора"""
    builder = InlineKeyboardBuilder()
    builder.button(text=f"📋 Заявки ({requests_count})", callback_data="admin_requests")
    builder.button(text="👥 DM пользователи", callback_data="admin_dms")
    builder.button(text="👤 Мой профиль", callback_data="my_profile")
    builder.button(text="📈 Статистика", callback_data="admin_stats")
    builder.button(text="📥 Экспорт в Excel", callback_data="admin_export_stats")
    builder.adjust(2, 2, 1)
    return builder.as_markup()

def get_dm_keyboard(has_tg: bool = False, has_profile: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура DM менеджера (ограниченная)"""
    builder = InlineKeyboardBuilder()

    if not has_tg:
        builder.button(text="➕ Подключить ТГ аккаунт", callback_data="dm_add_tg")
    else:
        builder.button(text="🔄 Переподключить ТГ", callback_data="dm_reconnect_tg")

    if has_profile:
        builder.button(text="✏️ Настроить фразы", callback_data="dm_edit_phrases")
    else:
        builder.button(text="📝 Заполнить профиль", callback_data="dm_fill_profile")

    builder.button(text="📊 Моя статистика", callback_data="dm_my_stats")
    builder.adjust(1)
    return builder.as_markup()

def get_approve_keyboard(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Одобрить", callback_data=f"approve_{user_id}")
    builder.button(text="❌ Отклонить", callback_data=f"reject_{user_id}")
    return builder.as_markup()

def get_profile_keyboard(has_profile: bool = False, is_admin: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if is_admin:
        builder.button(text="🔙 В меню админа", callback_data="admin_back")
    else:
        if has_profile:
            builder.button(text="✏️ Изменить тег", callback_data="edit_tag")
            builder.button(text="✏️ Настроить фразы", callback_data="dm_edit_phrases")
        else:
            builder.button(text="📝 Создать профиль DM", callback_data="fill_profile")
        builder.button(text="🔙 Назад", callback_data="dm_back")

    builder.adjust(2, 1)
    return builder.as_markup()

def get_back_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="admin_back")
    return builder.as_markup()

def get_dm_back_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="dm_back")
    return builder.as_markup()

def get_dms_list_keyboard(dms: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for dm in dms:
        builder.button(text=f"👤 {dm['username'] or dm['tg_id']}", callback_data=f"dm_profile_{dm['tg_id']}")
    builder.adjust(1)
    builder.button(text="🔙 Назад", callback_data="admin_back")
    return builder.as_markup()

def get_dm_profile_keyboard(has_tg: bool = False) -> InlineKeyboardMarkup:
    """Профиль ДМ для админа (редактирование)"""
    builder = InlineKeyboardBuilder()
    if not has_tg:
        builder.button(text="➕ Подключить ТГ аккаунт", callback_data="dm_add_tg")
    else:
        builder.button(text="🔄 Переподключить ТГ", callback_data="dm_reconnect_tg")

    builder.button(text="✏️ Настроить фразы", callback_data="dm_edit_phrases_admin")
    builder.button(text="🔙 Назад", callback_data="admin_dms")
    builder.adjust(1)
    return builder.as_markup()

def get_tg_auth_start_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="dm_back_to_profile")
    return builder.as_markup()

def get_dm_phrases_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для настройки фраз трекинга"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Сообщения после приветствия", callback_data="dm_edit_greeting_msgs")
    builder.button(text="✏️ Сообщение на додеп", callback_data="dm_edit_dodep_msg")
    builder.button(text="🔙 Назад", callback_data="dm_back")
    builder.adjust(1)
    return builder.as_markup()

def get_dm_phrases_admin_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для настройки фраз трекинга (админ)"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Сообщения после приветствия", callback_data="dm_edit_greeting_msgs_admin")
    builder.button(text="✏️ Сообщение на додеп", callback_data="dm_edit_dodep_msg_admin")
    builder.button(text="🔙 Назад", callback_data="admin_dms")
    builder.adjust(1)
    return builder.as_markup()

def get_greeting_msgs_edit_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Сообщение 1", callback_data="dm_edit_greeting_1")
    builder.button(text="✏️ Сообщение 2", callback_data="dm_edit_greeting_2")
    builder.button(text="🔙 Назад", callback_data="dm_edit_phrases")
    builder.adjust(1)
    return builder.as_markup()

def get_greeting_msgs_edit_admin_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Сообщение 1", callback_data="dm_edit_greeting_1_admin")
    builder.button(text="✏️ Сообщение 2", callback_data="dm_edit_greeting_2_admin")
    builder.button(text="🔙 Назад", callback_data="dm_edit_phrases_admin")
    builder.adjust(1)
    return builder.as_markup()
