"""Тексты и инлайн-клавиатуры бота (приветствие + настройки).

Презентация отделена от оркестрации (run.py) и состояния (settings.py),
чтобы тексты и разбор callback можно было проверять без Telegram.
"""
from telethon import Button

WELCOME = (
    "\U0001F575 Привет! Это <b>DIASLOG INTERCEPT</b>.\n\n"
    "Я приношу тебе то, что пытаются скрыть в Telegram:\n"
    "\U0001F5D1 удалённые сообщения\n"
    "✏️ изменённые сообщения\n"
    "\U0001F441 одноразовые фото и видео"
)

# callback data (bytes) для инлайн-кнопок
CB_OPEN = b"open_settings"
CB_BACK = b"back"
_TOGGLE_PREFIX = b"toggle:"
CB_TOGGLE = {
    "enabled": _TOGGLE_PREFIX + b"enabled",
    "deleted": _TOGGLE_PREFIX + b"deleted",
    "edited": _TOGGLE_PREFIX + b"edited",
    "view_once": _TOGGLE_PREFIX + b"view_once",
    "groups": _TOGGLE_PREFIX + b"groups",
}



# admin callback data
CB_ADMIN_OPEN = b"admin:open"
CB_ADMIN_ADD = b"admin:add"
CB_ADMIN_REMOVE = b"admin:remove"
CB_ADMIN_CANCEL = b"admin:cancel"
_ADMIN_RM_PREFIX = b"admin:rm:"
_ADMIN_RMOK_PREFIX = b"admin:rmok:"
CB_CONNECT = b"connect"
CB_CONN_CODE = b"conn:code"
CB_CONN_QR = b"conn:qr"
_ADMIN_SIMPLE = {
    CB_ADMIN_OPEN: ("open", None),
    CB_ADMIN_ADD: ("add", None),
    CB_ADMIN_REMOVE: ("remove", None),
    CB_ADMIN_CANCEL: ("cancel", None),
}


def parse_admin(data):
    """Разбор admin-callback: (action, arg) или None.
    action ∈ open/add/remove/cancel/rm/rmok; arg — имя аккаунта для rm/rmok."""
    if data in _ADMIN_SIMPLE:
        return _ADMIN_SIMPLE[data]
    if data.startswith(_ADMIN_RMOK_PREFIX):
        return ("rmok", data[len(_ADMIN_RMOK_PREFIX):].decode())
    if data.startswith(_ADMIN_RM_PREFIX):
        return ("rm", data[len(_ADMIN_RM_PREFIX):].decode())
    return None


def parse_toggle(data):
    """b'toggle:deleted' -> 'deleted'; для прочего -> None."""
    if data.startswith(_TOGGLE_PREFIX):
        return data[len(_TOGGLE_PREFIX):].decode()
    return None


def welcome_buttons(is_admin=False):
    rows = [
        [Button.inline("⚙️ Настройки", CB_OPEN)],
        [Button.inline("🔌 Подключиться", CB_CONNECT)],
    ]
    if is_admin:
        rows.append([Button.inline("🛠 Админ-панель", CB_ADMIN_OPEN)])
    return rows


def _mark(on):
    return "✅" if on else "❌"


def settings_text(label, s):
    head = "🟢 Бот включён" if s.enabled else "🔴 Бот выключен — перехватов нет"
    return (
        f"⚙️ <b>Настройки</b> — аккаунт «{label}»\n\n"
        f"{head}\n\n"
        "Что присылать при перехвате:"
    )


def settings_buttons(s):
    master = "🔴 Выключить бота" if s.enabled else "🟢 Включить бота"
    return [
        [Button.inline(master, CB_TOGGLE["enabled"])],
        [Button.inline(f"🗑 Удалённые {_mark(s.deleted)}", CB_TOGGLE["deleted"]),
         Button.inline(f"✏️ Изменённые {_mark(s.edited)}", CB_TOGGLE["edited"])],
        [Button.inline(f"👁 Одноразовые {_mark(s.view_once)}", CB_TOGGLE["view_once"]),
         Button.inline(f"👥 Группы {_mark(s.groups)}", CB_TOGGLE["groups"])],
        [Button.inline("◀️ Назад", CB_BACK)],
    ]


def admin_text(labels):
    body = "\n".join(f"• {l}" for l in labels) or "(пусто)"
    return (
        "🛠 <b>Админ-панель</b>\n"
        f"Аккаунтов под наблюдением: {len(labels)}\n\n"
        f"{body}"
    )


def admin_buttons():
    return [
        [Button.inline("➕ Добавить аккаунт", CB_ADMIN_ADD)],
        [Button.inline("➖ Удалить аккаунт", CB_ADMIN_REMOVE)],
        [Button.inline("◀️ Назад", CB_BACK)],
    ]


def remove_list_buttons(items):
    """items: [(name, label)] — кнопка на каждый аккаунт + Назад."""
    rows = [[Button.inline(f"➖ {label}", _ADMIN_RM_PREFIX + name.encode())]
            for name, label in items]
    rows.append([Button.inline("◀️ Назад", CB_ADMIN_OPEN)])
    return rows


def confirm_remove_buttons(name):
    return [
        [Button.inline("✅ Да, удалить", _ADMIN_RMOK_PREFIX + name.encode())],
        [Button.inline("◀️ Отмена", CB_ADMIN_OPEN)],
    ]


def wizard_cancel_buttons():
    return [[Button.inline("Отмена", CB_ADMIN_CANCEL)]]


def connect_method_text():
    return (
        "Как подключить аккаунт?\n\n"
        "📱 <b>По коду</b> — на одном телефоне (код придёт в твой Telegram).\n"
        "🖥 <b>По QR</b> — без кода, но нужен второй экран для сканирования."
    )


def connect_method_buttons():
    return [
        [Button.inline("📱 По коду", CB_CONN_CODE)],
        [Button.inline("🖥 По QR", CB_CONN_QR)],
        [Button.inline("◀️ Отмена", CB_ADMIN_CANCEL)],
    ]
