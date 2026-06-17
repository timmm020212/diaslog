"""Админ-панель: добавление/удаление отслеживаемых аккаунтов через бота.

Подключение аккаунта — на выбор:
  • по коду (один телефон, код приходит в Telegram самого аккаунта);
  • по QR / login-токену (без кода, но скан со второго устройства).
Перехваты добавленного аккаунта идут его владельцу (= самому аккаунту).
Динамические аккаунты сохраняются как .env.<id> на /data (переживают перезапуск).
"""
import os
import io
import time
import asyncio
import logging

import qrcode
from telethon import TelegramClient
from telethon.errors import (SessionPasswordNeededError, PhoneCodeInvalidError,
                             PhoneCodeExpiredError, PhoneNumberInvalidError,
                             FloodWaitError)

import profiles
import bot_ui
from settings import Settings
from capturer import Capturer

log = logging.getLogger("diaslog.admin")

QR_TOKEN_TIMEOUT = 30   # сек на одно ожидание скана до пересоздания токена
QR_TOTAL_TIMEOUT = 180  # сек общий лимит на подтверждение входа по QR


def extract_code(text):
    """Выпарить цифры кода из 'разбитого' ввода ('1 2 3 4 5' -> '12345')."""
    return "".join(ch for ch in text if ch.isdigit())


class Wizard:
    """Состояние одного входа (режим code или qr)."""

    def __init__(self, client, mode):
        self.client = client
        self.mode = mode          # "code" | "qr"
        self.step = "phone" if mode == "code" else "qr"
        # code-режим:
        self.phone = None
        self.phone_code_hash = None
        # qr-режим:
        self.qr = None
        self.qr_msg = None
        self.qr_task = None


class AccountManager:
    def __init__(self, bot, bot_cfg, store_factory):
        self.bot = bot
        self.bot_cfg = bot_cfg
        self.store_factory = store_factory
        self.accounts = {}    # name -> Capturer
        self.registry = {}    # owner_id -> (label, Settings)
        self.wizards = {}     # user_id -> Wizard

    # ---------- жизненный цикл аккаунтов ----------
    async def start_profile(self, profile):
        st = Settings.load(profile.settings_path)
        cap = Capturer(profile, self.store_factory, bot_client=self.bot, settings=st)
        await cap.start()
        self.accounts[profile.name] = cap
        self.registry[profile.owner_id] = (cap.me_name or profile.label, st)
        return cap

    async def remove(self, name):
        cap = self.accounts.pop(name, None)
        if cap is None:
            return False
        self.registry.pop(cap.profile.owner_id, None)
        try:
            await cap.stop()
        except Exception as e:
            log.warning("остановка %s: %s", name, e)
        profiles.delete_profile(cap.profile)
        return True

    def list_items(self):
        return [(name, cap.me_name or cap.profile.label)
                for name, cap in self.accounts.items()]

    def labels(self):
        return [label for _, label in self.list_items()]

    def _new_client(self, user_id):
        session = os.path.join(profiles.CONFIG_DIR, f".login_{user_id}")
        return TelegramClient(session, self.bot_cfg.api_id, self.bot_cfg.api_hash)

    # ---------- подключение по коду ----------
    async def begin_code(self, user_id):
        await self.cancel(user_id)
        client = self._new_client(user_id)
        await client.connect()
        self.wizards[user_id] = Wizard(client, "code")
        return ("Пришли <b>номер телефона</b> аккаунта (с +). Код придёт в Telegram "
                "этого аккаунта — в чат «Telegram».")

    async def feed_message(self, user_id, text):
        wiz = self.wizards.get(user_id)
        if wiz is None:
            return None
        try:
            if wiz.step == "phone":
                wiz.phone = text.strip()
                sent = await wiz.client.send_code_request(wiz.phone)
                wiz.phone_code_hash = sent.phone_code_hash
                wiz.step = "code"
                return ("Код пришёл в Telegram этого аккаунта (чат «Telegram»). "
                        "Введи его <b>разбито</b> — например <code>1 2 3 4 5</code>.")
            if wiz.step == "code":
                try:
                    await wiz.client.sign_in(phone=wiz.phone, code=extract_code(text),
                                             phone_code_hash=wiz.phone_code_hash)
                except SessionPasswordNeededError:
                    wiz.step = "password"
                    return "У аккаунта включена 2FA. Пришли пароль облака."
                except (PhoneCodeInvalidError, PhoneCodeExpiredError):
                    return "Код неверный или истёк. Введи ещё раз (разбито)."
                return await self._finalize(user_id, wiz)
            if wiz.step == "password":
                try:
                    await wiz.client.sign_in(password=text.strip())
                except FloodWaitError:
                    raise
                except Exception as e:
                    return f"Пароль не подошёл: {e}. Попробуй ещё раз."
                return await self._finalize(user_id, wiz)
        except FloodWaitError as e:
            await self.cancel(user_id)
            return f"Telegram просит подождать {e.seconds} c. Попробуй позже."
        except PhoneNumberInvalidError:
            await self.cancel(user_id)
            return "Неверный номер телефона. Начни заново."
        except Exception as e:
            await self.cancel(user_id)
            log.warning("вход по коду: %s", e)
            return f"Ошибка входа: {e}. Начни заново."
        return None

    # ---------- подключение по QR ----------
    @staticmethod
    def _qr_png(url):
        """PNG-картинка QR из ссылки tg://login (BytesIO для отправки/обновления)."""
        buf = io.BytesIO()
        qrcode.make(url).save(buf, "PNG")
        buf.seek(0)
        buf.name = "qr.png"
        return buf

    @staticmethod
    def _qr_caption(url):
        return (
            "Подтверди вход — <b>нужен второй экран</b>:\n\n"
            "1. Открой ЭТОТ чат на втором устройстве (компьютер web.telegram.org "
            "или другой телефон).\n"
            "2. На телефоне добавляемого аккаунта: Настройки → Устройства → "
            "Подключить устройство → отсканируй этот QR.\n\n"
            "QR обновляется сам (~30 c)."
        )

    async def begin_qr(self, user_id):
        await self.cancel(user_id)
        client = self._new_client(user_id)
        await client.connect()
        wiz = Wizard(client, "qr")
        self.wizards[user_id] = wiz
        try:
            wiz.qr = await client.qr_login()
        except Exception as e:
            await self.cancel(user_id)
            await self.bot.send_message(user_id, f"Не удалось начать вход: {e}")
            return
        wiz.qr_msg = await self.bot.send_file(
            user_id, self._qr_png(wiz.qr.url), caption=self._qr_caption(wiz.qr.url),
            parse_mode="html", buttons=bot_ui.wizard_cancel_buttons())
        wiz.qr_task = asyncio.create_task(self._qr_loop(user_id, wiz))

    async def _qr_loop(self, user_id, wiz):
        end = time.monotonic() + QR_TOTAL_TIMEOUT
        try:
            while time.monotonic() < end:
                try:
                    await wiz.qr.wait(timeout=QR_TOKEN_TIMEOUT)
                except asyncio.TimeoutError:
                    await wiz.qr.recreate()
                    try:
                        await wiz.qr_msg.edit(self._qr_caption(wiz.qr.url),
                                              file=self._qr_png(wiz.qr.url),
                                              parse_mode="html")
                    except Exception as e:
                        log.warning("обновление QR: %s", e)
                    continue
                except SessionPasswordNeededError:
                    wiz.step = "password"
                    await self.bot.send_message(
                        user_id, "У аккаунта включена 2FA. Пришли пароль облака.",
                        buttons=bot_ui.wizard_cancel_buttons())
                    return
                reply = await self._finalize(user_id, wiz)
                await self.bot.send_message(user_id, reply, parse_mode="html")
                return
            await self._cleanup(user_id)
            await self.bot.send_message(
                user_id, "⏳ Время вышло, вход не подтверждён. Нажми «Подключиться» заново.")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("QR-вход: %s", e)
            await self._cleanup(user_id)
            await self.bot.send_message(user_id, f"Ошибка входа: {e}. Попробуй заново.")

    async def _finalize(self, user_id, wiz):
        me = await wiz.client.get_me()
        try:
            await wiz.client.disconnect()
        except Exception:
            pass
        self.wizards.pop(user_id, None)
        name = f"id{me.id}"
        if name in self.accounts:
            await self.remove(name)
        env_path = profiles.write_profile_env(
            name, self.bot_cfg.api_id, self.bot_cfg.api_hash, me.id)
        profile = profiles.Profile(name, env_path)
        login_session = os.path.join(profiles.CONFIG_DIR, f".login_{user_id}.session")
        try:
            os.replace(login_session, profile.user_session + ".session")
        except OSError as e:
            log.warning("перенос сессии: %s", e)
        for suffix in ("-journal", "-wal", "-shm"):
            src = login_session + suffix
            if os.path.exists(src):
                try:
                    os.replace(src, profile.user_session + ".session" + suffix)
                except OSError:
                    pass
        cap = await self.start_profile(profile)
        label = cap.me_name or name
        return (f"✅ Аккаунт <b>{label}</b> подключён. Перехваты (удалённые/изменённые) "
                "будут приходить сюда.")

    async def _cleanup(self, user_id):
        """Снять визард и почистить временные файлы. НЕ трогает фоновую задачу."""
        wiz = self.wizards.pop(user_id, None)
        if wiz is None:
            return
        try:
            await wiz.client.disconnect()
        except Exception:
            pass
        base = os.path.join(profiles.CONFIG_DIR, f".login_{user_id}.session")
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                os.remove(base + suffix)
            except OSError:
                pass

    async def cancel(self, user_id):
        """Отмена снаружи (кнопка/ресет): гасит фоновую задачу QR и чистит."""
        wiz = self.wizards.get(user_id)
        if wiz is None:
            return False
        if wiz.qr_task is not None:
            wiz.qr_task.cancel()
        await self._cleanup(user_id)
        return True
