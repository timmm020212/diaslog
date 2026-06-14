"""Кэш входящих сообщений (SQLite) для одного профиля.

Нужен, чтобы восстановить удалённые/изменённые сообщения: входящие складываем сюда,
а при удалении/правке достаём прежний текст и медиа. Доставка идёт ботом прямо
в Telegram (см. capturer.py) — отдельного веб-архива больше нет.
"""
import os
import time
import sqlite3

# ID супергрупп/каналов имеют вид -100xxxxxxxxxx (меньше этого порога).
CHANNEL_THRESHOLD = -1000000000000


def _connect(db_path):
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
    conn.execute("PRAGMA busy_timeout=8000")  # ждать блокировку, а не падать сразу
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


class Store:
    def __init__(self, db_path):
        self.db_path = db_path
        self._conn = _connect(db_path)
        self._init()

    def _init(self):
        c = self._conn
        cols = [r[1] for r in c.execute("PRAGMA table_info(messages)")]
        if cols and "chat_title" not in cols:
            c.execute("DROP TABLE messages")  # старая схема — пересоздаём (это кэш)
            cols = []
        if not cols:
            c.execute(
                """
                CREATE TABLE messages (
                    chat_id INTEGER, msg_id INTEGER,
                    sender_id INTEGER, sender_name TEXT, chat_title TEXT,
                    text TEXT, media_path TEXT, media_type TEXT,
                    date TEXT, created_at REAL,
                    PRIMARY KEY (chat_id, msg_id)
                )
                """
            )
        c.commit()

    def save_message(self, chat_id, msg_id, sender_id, sender_name, chat_title,
                     text, media_path, media_type, date):
        self._conn.execute(
            "INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?,?,?,?,?)",
            (chat_id, msg_id, sender_id, sender_name, chat_title,
             text, media_path, media_type, date, time.time()),
        )
        self._conn.commit()

    def get_message(self, chat_id, msg_id):
        r = self._conn.execute(
            "SELECT chat_id, msg_id, sender_id, sender_name, chat_title, text, "
            "media_path, media_type, date FROM messages WHERE chat_id=? AND msg_id=?",
            (chat_id, msg_id),
        ).fetchone()
        return _msg_row(r)

    def get_nonchannel_message(self, msg_id):
        r = self._conn.execute(
            "SELECT chat_id, msg_id, sender_id, sender_name, chat_title, text, "
            "media_path, media_type, date FROM messages "
            "WHERE msg_id=? AND chat_id > ? ORDER BY created_at DESC LIMIT 1",
            (msg_id, CHANNEL_THRESHOLD),
        ).fetchone()
        return _msg_row(r)

    def update_text(self, chat_id, msg_id, text):
        self._conn.execute("UPDATE messages SET text=? WHERE chat_id=? AND msg_id=?",
                           (text, chat_id, msg_id))
        self._conn.commit()

    def cleanup(self, retention_days):
        """Чистит старый кэш сообщений и скачанные медиа старше retention_days."""
        cutoff = time.time() - retention_days * 86400
        rows = self._conn.execute(
            "SELECT media_path FROM messages WHERE created_at < ?", (cutoff,)
        ).fetchall()
        for (path,) in rows:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        self._conn.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
        self._conn.commit()


def _msg_row(r):
    if not r:
        return None
    keys = ["chat_id", "msg_id", "sender_id", "sender_name", "chat_title",
            "text", "media_path", "media_type", "date"]
    return dict(zip(keys, r))
