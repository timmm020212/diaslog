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


def parse_toggle(data):
    """b'toggle:deleted' -> 'deleted'; для прочего -> None."""
    if data.startswith(_TOGGLE_PREFIX):
        return data[len(_TOGGLE_PREFIX):].decode()
    return None


def welcome_buttons():
    return [[Button.inline("⚙️ Настройки", CB_OPEN)]]


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
