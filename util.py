"""Мелкие помощники, общие для юзербота."""
import html as _html

from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument


def real_name(sender):
    """Имя человека без @username (ник идёт отдельным полем)."""
    if sender is None:
        return "Неизвестный"
    name = " ".join(filter(None, [
        getattr(sender, "first_name", None),
        getattr(sender, "last_name", None),
    ]))
    if not name:
        name = getattr(sender, "username", None) or str(getattr(sender, "id", "?"))
    return name


def username_of(sender):
    return getattr(sender, "username", None) if sender is not None else None


def display_name(sender):
    """Имя + @username одной строкой (для логов и приветствий)."""
    name = real_name(sender)
    username = username_of(sender)
    if username and username not in name:
        name += f" (@{username})"
    return name


def html_escape(s):
    return _html.escape(s or "", quote=False)


def mention_html(name, username):
    """«<b>Ник</b> (@username)» — имя жирным, ник в скобках (если есть)."""
    out = f"<b>{html_escape(name)}</b>"
    if username:
        out += f" (@{html_escape(username)})"
    return out


_MEDIA_LABELS = {
    "photo": "фото", "video": "видео", "voice": "голосовое",
    "video_note": "кружок", "sticker": "стикер", "gif": "GIF",
    "document": "файл", "other": "медиа",
}


def media_label(kind):
    return _MEDIA_LABELS.get(kind, "медиа")


def media_kind(msg):
    if not msg.media:
        return None
    if isinstance(msg.media, MessageMediaPhoto):
        return "photo"
    if isinstance(msg.media, MessageMediaDocument):
        if msg.sticker:
            return "sticker"
        if msg.video_note:
            return "video_note"
        if msg.video:
            return "video"
        if msg.voice:
            return "voice"
        if msg.gif:
            return "gif"
        return "document"
    return "other"


def is_view_once(msg):
    """Одноразовое / самоуничтожающееся медиа имеет ttl_seconds."""
    return bool(msg.media and getattr(msg.media, "ttl_seconds", None))
