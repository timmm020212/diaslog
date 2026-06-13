"""Мелкие помощники, общие для юзербота."""
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument


def display_name(sender):
    if sender is None:
        return "Неизвестный"
    name = " ".join(filter(None, [
        getattr(sender, "first_name", None),
        getattr(sender, "last_name", None),
    ]))
    username = getattr(sender, "username", None)
    if not name:
        name = username or str(getattr(sender, "id", "?"))
    elif username:
        name += f" (@{username})"
    return name


def media_kind(msg):
    if not msg.media:
        return None
    if isinstance(msg.media, MessageMediaPhoto):
        return "photo"
    if isinstance(msg.media, MessageMediaDocument):
        if msg.video:
            return "video"
        if msg.voice:
            return "voice"
        if msg.video_note:
            return "video_note"
        return "document"
    return "other"


def is_view_once(msg):
    """Одноразовое / самоуничтожающееся медиа имеет ttl_seconds."""
    return bool(msg.media and getattr(msg.media, "ttl_seconds", None))
