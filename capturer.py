"""Capturer — один аккаунт: юзербот ловит события, бот доставляет их владельцу в Telegram.

Один экземпляр на профиль. start()/stop() управляют подключением.
В терминале (первый вход) используется start(allow_login=True).
В веб-режиме — start() с уже готовой сессией.
"""
import os
import asyncio
import logging

from telethon import TelegramClient, events

import util

log = logging.getLogger("diaslog.capturer")


class Capturer:
    def __init__(self, profile, store_factory):
        self.profile = profile
        self._store_factory = store_factory  # вызывается в потоке с циклом asyncio
        self.store = None
        self.user_client = None
        self.bot_client = None
        self.running = False
        self.me_name = None
        self.last_error = None
        self._cleanup_task = None

    # ---------- доставка через бота ----------
    async def _send_text(self, text):
        if not self.profile.owner_id:
            log.warning("[%s] OWNER_ID не задан — напиши боту /start", self.profile.name)
            return
        try:
            await self.bot_client.send_message(self.profile.owner_id, text,
                                               parse_mode=None, link_preview=False)
        except Exception as e:
            log.warning("[%s] ошибка доставки текста: %s", self.profile.name, e)

    async def _send_media(self, path, caption):
        if not self.profile.owner_id:
            return
        try:
            await self.bot_client.send_file(self.profile.owner_id, path,
                                           caption=caption[:1024], parse_mode=None)
        except Exception as e:
            log.warning("[%s] ошибка доставки медиа: %s", self.profile.name, e)
            await self._send_text(caption + "\n(медиа не удалось отправить)")

    async def _download(self, msg):
        try:
            return await self.user_client.download_media(msg, file=self.profile.media_dir)
        except Exception as e:
            log.warning("[%s] не удалось скачать медиа: %s", self.profile.name, e)
            return None

    # ---------- обработчики ----------
    async def _on_new(self, event):
        if not (event.is_private or event.is_group):
            return
        msg = event.message
        sender = await event.get_sender()
        sname = util.display_name(sender)
        kind = util.media_kind(msg)
        media_path = None

        chat_title = None
        if event.is_group:
            chat = await event.get_chat()
            chat_title = getattr(chat, "title", None)

        if util.is_view_once(msg):
            if not event.is_private:
                return  # в группах одноразовые медиа не ловим
            media_path = await self._download(msg)
            caption = f"\U0001F441 Одноразовое медиа от {sname}"
            if msg.message:
                caption += f"\nПодпись: {msg.message}"
            if media_path:
                await self._send_media(media_path, caption)
            else:
                await self._send_text(caption + "\n(не удалось скачать)")
        elif self.profile.cache_media and kind in (
                "photo", "video", "voice", "video_note", "document"):
            media_path = await self._download(msg)

        self.store.save_message(
            chat_id=event.chat_id, msg_id=msg.id,
            sender_id=getattr(sender, "id", None), sender_name=sname,
            chat_title=chat_title, text=msg.message or "",
            media_path=media_path, media_type=kind, date=str(msg.date),
        )

    async def _on_deleted(self, event):
        chat_id = event.chat_id  # для супергрупп/каналов задан, иначе None
        for msg_id in event.deleted_ids:
            if chat_id is not None:
                row = self.store.get_message(chat_id, msg_id)
            else:
                row = self.store.get_nonchannel_message(msg_id)
            if not row:
                continue
            where = f" в «{row['chat_title']}»" if row["chat_title"] else ""
            body = f"\U0001F5D1 Удалено{where} — {row['sender_name']}"
            if row["text"]:
                body += f"\n\n{row['text']}"
            media_path = row["media_path"]
            if media_path and os.path.exists(media_path):
                await self._send_media(media_path, body)
            else:
                if row["media_type"] and not row["text"]:
                    body += f"\n[медиа: {row['media_type']}]"
                await self._send_text(body)

    async def _on_edited(self, event):
        if not (event.is_private or event.is_group):
            return
        msg = event.message
        new_text = msg.message or ""
        row = self.store.get_message(event.chat_id, msg.id)
        if row is not None:
            old_text = row["text"] or ""
            if old_text != new_text:
                where = f" в «{row['chat_title']}»" if row["chat_title"] else ""
                await self._send_text(
                    f"✏ Изменено{where} — {row['sender_name']}\n\n"
                    f"Было:\n{old_text or '(пусто)'}\n\nСтало:\n{new_text or '(пусто)'}"
                )
            self.store.update_text(event.chat_id, msg.id, new_text)
        else:
            sender = await event.get_sender()
            chat_title = None
            if event.is_group:
                chat = await event.get_chat()
                chat_title = getattr(chat, "title", None)
            self.store.save_message(
                event.chat_id, msg.id, getattr(sender, "id", None),
                util.display_name(sender), chat_title, new_text, None,
                util.media_kind(msg), str(msg.date),
            )

    async def _on_bot_start(self, event):
        await event.reply(
            f"Привет! Это бот-доставщик Diaslog Spy.\n\nТвой ID: {event.sender_id}\n"
            f"Впиши его в .env как OWNER_ID и перезапусти."
        )

    # ---------- жизненный цикл ----------
    async def start(self, allow_login=False):
        if self.running:
            return
        self.last_error = None
        self.store = self._store_factory(self.profile.db_path)
        self.user_client = TelegramClient(
            self.profile.user_session, self.profile.api_id, self.profile.api_hash)
        self.bot_client = TelegramClient(
            self.profile.bot_session, self.profile.api_id, self.profile.api_hash)

        self.user_client.add_event_handler(self._on_new, events.NewMessage(incoming=True))
        self.user_client.add_event_handler(self._on_deleted, events.MessageDeleted())
        self.user_client.add_event_handler(self._on_edited, events.MessageEdited(incoming=True))
        self.bot_client.add_event_handler(self._on_bot_start, events.NewMessage(pattern="/start"))

        await self.bot_client.start(bot_token=self.profile.bot_token)

        if allow_login:
            await self.user_client.start()  # интерактивный вход (терминал)
        else:
            await self.user_client.connect()
            if not await self.user_client.is_user_authorized():
                await self.user_client.disconnect()
                await self.bot_client.disconnect()
                self.last_error = "Нет сессии. Войди один раз через терминал: python main.py " + (
                    "" if self.profile.name == "default" else self.profile.name)
                raise RuntimeError(self.last_error)

        me = await self.user_client.get_me()
        self.me_name = util.display_name(me)
        self.running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        log.info("[%s] запущен как %s", self.profile.name, self.me_name)

        if self.profile.owner_id:
            try:
                await self.bot_client.send_message(
                    self.profile.owner_id, "✅ Diaslog Spy bot запущен и следит за чатами.")
            except Exception as e:
                log.warning("[%s] не написать владельцу (нажми /start): %s",
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
        try:
            await self.bot_client.disconnect()
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
