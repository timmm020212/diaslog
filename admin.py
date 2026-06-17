"""Админ-панель: добавление/удаление отслеживаемых аккаунтов через бота.

Добавление — вход по login-токену Telegram (ссылка tg://login + QR), без SMS-кодов:
бот шлёт сообщение со ссылкой «Подтвердить вход» и QR того же токена; владелец
подтверждает вход со своего телефона. Перехваты добавленного аккаунта идут его
владельцу. Динамические аккаунты сохраняются как .env.<id> на /data (переживают
перезапуск — их подхватывает profiles.discover()).
"""
import os
import io
import time
import asyncio
import logging

import qrcode
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

import profiles
import bot_ui
from settings import Settings
from capturer import Capturer

log = logging.getLogger("diaslog.admin")

QR_TOKEN_TIMEOUT = 30   # сек на одно ожидание скана до пересоздания токена
QR_TOTAL_TIMEOUT = 180  # сек общий лимит на подтверждение входа


class Wizard:
    """Состояние одного входа по ссылке/QR."""

    def __init__(self, client):
        self.client = client
        self.qr = None       # QRLogin
        self.qr_msg = None    # сообщение с QR (обновляем при пересоздании токена)
        self.qr_task = None   # фоновая задача ожидания подтверждения
        self.step = "qr"      # qr -> password


class AccountManager:
    def __init__(self, bot, bot_cfg, store_factory):
        self.bot = bot
        self.bot_cfg = bot_cfg
        self.store_factory = store_factory
        self.accounts = {}    # name -> Capturer
        self.registry = {}    # owner_id -> (label, Settings)
        self.wizards = {}     # admin_id -> Wizard

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

    # ---------- визард входа по ссылке/QR ----------
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
            "Добавление аккаунта — подтверди вход:\n\n"
            f"• На <b>этом</b> телефоне — нажми <a href=\"{url}\">✅ Подтвердить вход</a>\n"
            "• С <b>другого</b> устройства — отсканируй QR: "
            "Настройки → Устройства → Подключить устройство\n\n"
            "Ссылка живёт ~30 c и обновляется сама."
        )

    async def begin_add(self, admin_id):
        await self.cancel(admin_id)
        session = os.path.join(profiles.CONFIG_DIR, f".login_{admin_id}")
        client = TelegramClient(session, self.bot_cfg.api_id, self.bot_cfg.api_hash)
        await client.connect()
        wiz = Wizard(client)
        self.wizards[admin_id] = wiz
        try:
            wiz.qr = await client.qr_login()
        except Exception as e:
            await self.cancel(admin_id)
            await self.bot.send_message(admin_id, f"Не удалось начать вход: {e}")
            return
        wiz.qr_msg = await self.bot.send_file(
            admin_id, self._qr_png(wiz.qr.url), caption=self._qr_caption(wiz.qr.url),
            parse_mode="html", buttons=bot_ui.wizard_cancel_buttons())
        wiz.qr_task = asyncio.create_task(self._qr_loop(admin_id, wiz))

    async def _qr_loop(self, admin_id, wiz):
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
                        admin_id, "У аккаунта включена 2FA. Пришли пароль облака.",
                        buttons=bot_ui.wizard_cancel_buttons())
                    return
                reply = await self._finalize(admin_id, wiz)
                await self.bot.send_message(admin_id, reply, parse_mode="html")
                return
            await self._cleanup(admin_id)
            await self.bot.send_message(
                admin_id, "⏳ Время вышло, вход не подтверждён. Нажми ➕ заново.")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("QR-вход: %s", e)
            await self._cleanup(admin_id)
            await self.bot.send_message(admin_id, f"Ошибка входа: {e}. Нажми ➕ заново.")

    async def feed_message(self, admin_id, text):
        wiz = self.wizards.get(admin_id)
        if wiz is None or wiz.step != "password":
            return None
        try:
            await wiz.client.sign_in(password=text.strip())
        except Exception as e:
            await self.cancel(admin_id)
            return f"Пароль не подошёл: {e}. Нажми ➕ заново."
        return await self._finalize(admin_id, wiz)

    async def _finalize(self, admin_id, wiz):
        me = await wiz.client.get_me()
        try:
            await wiz.client.disconnect()
        except Exception:
            pass
        self.wizards.pop(admin_id, None)
        name = f"id{me.id}"
        if name in self.accounts:
            await self.remove(name)
        env_path = profiles.write_profile_env(
            name, self.bot_cfg.api_id, self.bot_cfg.api_hash, me.id)
        profile = profiles.Profile(name, env_path)
        login_session = os.path.join(profiles.CONFIG_DIR, f".login_{admin_id}.session")
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
        return (f"✅ Аккаунт <b>{label}</b> добавлен. Перехваты пойдут владельцу — "
                "пусть нажмёт /start этому боту.")

    async def _cleanup(self, admin_id):
        """Снять визард и почистить временные файлы. НЕ трогает фоновую задачу
        (вызывается из самой задачи при таймауте/ошибке — нельзя отменять себя)."""
        wiz = self.wizards.pop(admin_id, None)
        if wiz is None:
            return
        try:
            await wiz.client.disconnect()
        except Exception:
            pass
        base = os.path.join(profiles.CONFIG_DIR, f".login_{admin_id}.session")
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                os.remove(base + suffix)
            except OSError:
                pass

    async def cancel(self, admin_id):
        """Отмена снаружи (кнопка/ресет перед новым входом): гасит фоновую задачу."""
        wiz = self.wizards.get(admin_id)
        if wiz is None:
            return False
        if wiz.qr_task is not None:
            wiz.qr_task.cancel()
        await self._cleanup(admin_id)
        return True
