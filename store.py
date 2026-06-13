"""Хранилище профиля (SQLite):
  * messages — кэш входящих (чтобы восстановить удалённые);
  * events   — лента пойманного (удалённые / изменённые / view-once) для веб-ленты.

Класс Store используется на стороне захвата (поток с Telethon).
Функции query_* / stats открывают своё подключение для чтения из веб-сервера.
"""
import os
import time
import sqlite3

# ID супергрупп/каналов имеют вид -100xxxxxxxxxx (меньше этого порога).
CHANNEL_THRESHOLD = -1000000000000

EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT, chat_id INTEGER, chat_title TEXT,
    sender_name TEXT, text TEXT, old_text TEXT,
    media_file TEXT, media_type TEXT, created_at REAL
)
"""


def _connect(db_path):
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
    conn.execute("PRAGMA busy_timeout=8000")  # ждать блокировку, а не падать сразу
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(EVENTS_DDL)  # гарантируем таблицу ленты и для чтения тоже
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
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT, chat_id INTEGER, chat_title TEXT,
                sender_name TEXT, text TEXT, old_text TEXT,
                media_file TEXT, media_type TEXT, created_at REAL
            )
            """
        )
        c.commit()

    # ---- кэш входящих ----
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

    # ---- лента событий ----
    def add_event(self, type_, chat_id, chat_title, sender_name,
                  text, old_text, media_file, media_type):
        self._conn.execute(
            "INSERT INTO events (type, chat_id, chat_title, sender_name, text, "
            "old_text, media_file, media_type, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (type_, chat_id, chat_title, sender_name, text, old_text,
             media_file, media_type, time.time()),
        )
        self._conn.commit()

    def cleanup(self, retention_days):
        """Чистит старый кэш сообщений. Файлы, на которые ссылается лента, не трогаем."""
        cutoff = time.time() - retention_days * 86400
        kept = {r[0] for r in self._conn.execute(
            "SELECT media_file FROM events WHERE media_file IS NOT NULL")}
        rows = self._conn.execute(
            "SELECT media_path FROM messages WHERE created_at < ?", (cutoff,)
        ).fetchall()
        for (path,) in rows:
            if path and os.path.basename(path) not in kept and os.path.exists(path):
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


# ---- чтение для веб-сервера (отдельное подключение) ----

_EVENT_KEYS = ["id", "type", "chat_id", "chat_title", "sender_name",
               "text", "old_text", "media_file", "media_type", "created_at"]


def query_events(db_path, type_="all", q="", after=0, limit=200):
    if not os.path.exists(db_path):
        return []
    conn = _connect(db_path)
    try:
        sql = ("SELECT id, type, chat_id, chat_title, sender_name, text, old_text, "
               "media_file, media_type, created_at FROM events WHERE id > ?")
        params = [int(after or 0)]
        if type_ and type_ != "all":
            sql += " AND type = ?"
            params.append(type_)
        if q:
            sql += " AND (text LIKE ? OR old_text LIKE ? OR sender_name LIKE ? OR chat_title LIKE ?)"
            like = f"%{q}%"
            params += [like, like, like, like]
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        rows = conn.execute(sql, params).fetchall()
        return [dict(zip(_EVENT_KEYS, r)) for r in rows]
    finally:
        conn.close()


def stats(db_path):
    out = {"total": 0, "deleted": 0, "edited": 0, "viewonce": 0, "today": 0}
    if not os.path.exists(db_path):
        return out
    conn = _connect(db_path)
    try:
        for type_, n in conn.execute("SELECT type, COUNT(*) FROM events GROUP BY type"):
            out[type_] = n
            out["total"] += n
        day_ago = time.time() - 86400
        out["today"] = conn.execute(
            "SELECT COUNT(*) FROM events WHERE created_at > ?", (day_ago,)
        ).fetchone()[0]
        return out
    finally:
        conn.close()
