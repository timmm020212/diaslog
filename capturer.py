"""Capturer — один аккаунт: юзербот ловит события, общий бот доставляет их владельцу.

Один экземпляр на профиль. Бот-доставщик ОДИН на всех — создаётся снаружи (run.py)
и передаётся в конструктор; каждый Capturer шлёт перехваты своему owner_id, поэтому
у разных владельцев в одном боте только свои уведомления.

start()/stop() управляют только юзербот-подключением (бот живёт отдельно).
В терминале (первый вход) используется start(allow_login=True).
"""
import os
import asyncio
import logging

from telethon import TelegramClient, events

import util
from settings import Settings

log = logging.getLogger("diaslog.capturer")

# Медиа, которые качаем у входящих, чтобы прислать удалённый файл.
CACHEABLE_MEDIA = ("photo", "video", "voice", "video_note", "document", "gif", "sticker")


class Capturer:
    def __init__(self, profile, store_factory, bot_client=None, settings=None):
        self.profile = profile
        self._store_factory = store_factory  # вызывается в потоке с циклом asyncio
        self.bot_client = bot_client  # общий бот-доставщик (один на всех); None при входе
        # Настройки фильтрации (общий объект с обработчиком кнопок в run.py).
        # None (терминальный вход) = всё включено по умолчанию.
        self.settings = settings or Settings(path=None)
        self.store = None
        self.user_client = None
        self.running = False
        self.me_name = None
        self.me_id = None  # id самого аккаунта — чтобы не реагировать на свои сообщения
        self.bot_id = None  # id бота-доставщика — чтобы не ловить его служебные сообщения
        self.last_error = None
        self._cleanup_task = None

    # ---------- доставка через бота ----------
    async def _send_text(self, text):
        if not self.bot_client or not self.profile.owner_id:
            return
        try:
            await self.bot_client.send_message(self.profile.owner_id, text,
                                               parse_mode="html", link_preview=False)
        except Exception as e:
            log.warning("[%s] ошибка доставки текста: %s", self.profile.name, e)

    async def _send_media(self, path, caption):
        if not self.bot_client or not self.profile.owner_id:
            return
        try:
            await self.bot_client.send_file(self.profile.owner_id, path,
                                           caption=caption[:1024], parse_mode="html")
        except Exception as e:
            log.warning("[%s] ошибка доставки медиа: %s", self.profile.name, e)
            await self._send_text(caption + "\n(медиа не удалось отправить ⚠️)")

    @staticmethod
    def _quote(text, limit=2000):
        """Текст в виде HTML-цитаты (или '(пусто)', если текста нет)."""
        inner = util.html_escape((text or "")[:limit]) or "(пусто)"
        return f"<blockquote>{inner}</blockquote>"

    async def _download(self, msg):
        try:
            return await self.user_client.download_media(msg, file=self.profile.media_dir)
        except Exception as e:
            log.warning("[%s] не удалось скачать медиа: %s", self.profile.name, e)
            return None

    # ---------- обработчики ----------
    async def _on_new(self, event):
        if not self.settings.enabled:
            return
        if self.bot_id is not None and event.chat_id == self.bot_id:
            return  # чат с самим ботом-доставщиком — его сообщения не перехватываем
        if not (event.is_private or event.is_group):
            return
        if event.is_group and not self.settings.groups:
            return  # слежка за группами выключена
        msg = event.message
        if msg.out:
            return  # своё исходящее сообщение — не наша добыча
        sender = await event.get_sender()
        if getattr(sender, "bot", False):
            return  # сообщения ботов (в т.ч. доставщика) не перехватываем
        sname = util.real_name(sender)
        uname = util.username_of(sender)
        kind = util.media_kind(msg)

        if util.is_view_once(msg):
            if not (self.settings.wants_view_once() and event.is_private):
                return  # одноразовые выключены или это группа — не трогаем
            media_path = await self._download(msg)
            head = (f"\U0001F441 {util.mention_html(sname, uname)} прислал(а) "
                    f"одноразовое {util.media_label(kind)} \U0001F525")
            caption = head + (f"\n\n{self._quote(msg.message)}" if msg.message else "")
            if media_path:
                await self._send_media(media_path, caption)
            else:
                await self._send_text(head + "\n(не удалось скачать ⚠️)")
            return  # одноразовое эфемерно — в кэш не кладём

        if not self.settings.cache_incoming():
            return  # ни удалённые, ни изменённые не нужны — не сохраняем

        chat_title = None
        if event.is_group:
            chat = await event.get_chat()
            chat_title = getattr(chat, "title", None)

        media_path = None
        if self.settings.wants_deleted() and self.profile.cache_media and kind in CACHEABLE_MEDIA:
            media_path = await self._download(msg)

        self.store.save_message(
            chat_id=event.chat_id, msg_id=msg.id,
            sender_id=getattr(sender, "id", None), sender_name=sname,
            sender_username=uname, chat_title=chat_title, text=msg.message or "",
            media_path=media_path, media_type=kind, date=str(msg.date),
        )

    async def _on_deleted(self, event):
        if not self.settings.wants_deleted():
            return
        chat_id = event.chat_id  # для супергрупп/каналов задан, иначе None
        for msg_id in event.deleted_ids:
            if chat_id is not None:
                row = self.store.get_message(chat_id, msg_id)
            else:
                row = self.store.get_nonchannel_message(msg_id)
            if not row:
                continue
            if self.bot_id is not None and row["sender_id"] == self.bot_id:
                continue  # сообщение бота-доставщика — не реагируем
            if row["sender_id"] == self.me_id:
                continue  # своё сообщение — не реагируем на собственные удаления
            if row["chat_title"] and not self.settings.groups:
                continue  # слежка за группами выключена (chat_title есть только у групп)
            who = util.mention_html(row["sender_name"], row["sender_username"])
            where = f" в «{util.html_escape(row['chat_title'])}»" if row["chat_title"] else ""
            typ = f" ({util.media_label(row['media_type'])})" if row["media_type"] else ""
            head = f"{who} удалил(а) 1 сообщение{typ}{where} \U0001F5D1"
            media_path = row["media_path"]
            if media_path and os.path.exists(media_path):
                caption = head + (f"\n\n{self._quote(row['text'])}" if row["text"] else "")
                await self._send_media(media_path, caption)
            else:
                body = head + (f"\n\n{self._quote(row['text'])}" if row["text"] else "")
                await self._send_text(body)

    async def _on_edited(self, event):
        if not self.settings.enabled:
            return
        if self.bot_id is not None and event.chat_id == self.bot_id:
            return  # бот-доставщик правит свои сообщения (кнопки/настройки) — не наша добыча
        if not (event.is_private or event.is_group):
            return
        if event.is_group and not self.settings.groups:
            return  # слежка за группами выключена
        msg = event.message
        if msg.out:
            return  # своё изменённое сообщение — не присылаем себе же
        sender = await event.get_sender()
        if getattr(sender, "bot", False):
            return  # бот (в т.ч. доставщик правит кнопки/настройки) — не наша добыча
        new_text = msg.message or ""
        row = self.store.get_message(event.chat_id, msg.id)
        if row is not None:
            old_text = row["text"] or ""
            if old_text != new_text:
                if self.settings.wants_edited():
                    who = util.mention_html(row["sender_name"], row["sender_username"])
                    where = f" в «{util.html_escape(row['chat_title'])}»" if row["chat_title"] else ""
                    await self._send_text(
                        f"✏️ {who} изменил(а) сообщение{where}\n\n"
                        f"Было:\n{self._quote(old_text)}\n"
                        f"Стало:\n{self._quote(new_text)}"
                    )
                self.store.update_text(event.chat_id, msg.id, new_text)  # держим кэш актуальным
        else:
            if not self.settings.cache_incoming():
                return
            chat_title = None
            if event.is_group:
                chat = await event.get_chat()
                chat_title = getattr(chat, "title", None)
            self.store.save_message(
                event.chat_id, msg.id, getattr(sender, "id", None),
                util.real_name(sender), util.username_of(sender), chat_title,
                new_text, None, util.media_kind(msg), str(msg.date),
            )

    # ---------- жизненный цикл ----------
    async def start(self, allow_login=False):
        if self.running:
            return
        self.last_error = None
        self.store = self._store_factory(self.profile.db_path)
        self.user_client = TelegramClient(
            self.profile.user_session, self.profile.api_id, self.profile.api_hash)

        self.user_client.add_event_handler(self._on_new, events.NewMessage(incoming=True))
        self.user_client.add_event_handler(self._on_deleted, events.MessageDeleted())
        self.user_client.add_event_handler(self._on_edited, events.MessageEdited(incoming=True))

        if allow_login:
            await self.user_client.start()  # интерактивный вход (терминал)
        else:
            await self.user_client.connect()
            if not await self.user_client.is_user_authorized():
                await self.user_client.disconnect()
                self.last_error = "Нет сессии. Войди один раз через терминал: python main.py " + (
                    "" if self.profile.name == "default" else self.profile.name)
                raise RuntimeError(self.last_error)

        me = await self.user_client.get_me()
        self.me_name = util.display_name(me)
        self.me_id = getattr(me, "id", None)
        if self.bot_client:
            try:
                bot_me = await self.bot_client.get_me()
                self.bot_id = getattr(bot_me, "id", None)
            except Exception as e:
                log.warning("[%s] не узнать id бота-доставщика: %s", self.profile.name, e)
        self.running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        log.info("[%s] запущен как %s", self.profile.name, self.me_name)

        if self.bot_client and self.profile.owner_id and self.settings.enabled:
            try:
                await self.bot_client.send_message(
                    self.profile.owner_id,
                    f"✅ Слежу за аккаунтом <b>{util.html_escape(self.me_name)}</b> "
                    f"— перехваты буду присылать сюда.", parse_mode="html")
            except Exception as e:
                log.warning("[%s] не написать владельцу (пусть нажмёт /start боту): %s",
                            self.profile.name, e)

    async def stop(self):
        if not self.running:
            return
        self.running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
        try:
            await self.user_client.disconnect()
        except Exception:
            pass
        log.info("[%s] остановлен", self.profile.name)

    async def _cleanup_loop(self):
        while self.running:
            try:
                self.store.cleanup(self.profile.retention_days)
            except Exception as e:
                log.warning("[%s] ошибка очистки: %s", self.profile.name, e)
            await asyncio.sleep(6 * 3600)

    async def run_terminal(self):
        """Терминальный режим: вход (если нужно) + работа до отключения."""
        await self.start(allow_login=True)
        log.info("[%s] слежу за чатами. Останов — Ctrl+C.", self.profile.name)
        await self.user_client.run_until_disconnected()
