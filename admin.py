"""Админ-панель: добавление/удаление отслеживаемых аккаунтов через бота.

AccountManager владеет жизненным циклом Capturer'ов и визардом входа
(телефон → код → 2FA). Перехваты добавленного аккаунта идут его владельцу
(id залогиненного аккаунта). Динамические аккаунты сохраняются как .env.<id>
на /data, поэтому переживают перезапуск (их подхватывает profiles.discover()).
"""
import os
import logging

from telethon import TelegramClient
from telethon.errors import (SessionPasswordNeededError, PhoneCodeInvalidError,
                             PhoneCodeExpiredError, PhoneNumberInvalidError,
                             FloodWaitError)

import profiles
from settings import Settings
from capturer import Capturer

log = logging.getLogger("diaslog.admin")


def extract_code(text):
    """Выпарить цифры кода из 'разбитого' ввода ('1 2 3 4 5' -> '12345')."""
    return "".join(ch for ch in text if ch.isdigit())


class Wizard:
    """Состояние одного входа: клиент Telethon и шаг диалога."""

    def __init__(self, client):
        self.client = client
        self.phone = None
        self.phone_code_hash = None
        self.step = "phone"  # phone -> code -> password


class AccountManager:
    def __init__(self, bot, bot_cfg, store_factory):
        self.bot = bot
        self.bot_cfg = bot_cfg
        self.store_factory = store_factory
        self.accounts = {}    # name -> Capturer
        self.registry = {}    # owner_id -> (label, Settings) — общий с настройками
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
        """[(name, label)] для текста и кнопок."""
        return [(name, cap.me_name or cap.profile.label)
                for name, cap in self.accounts.items()]

    def labels(self):
        return [label for _, label in self.list_items()]

    # ---------- визард добавления ----------
    async def begin_add(self, admin_id):
        await self.cancel(admin_id)  # сбросить прежний визард, если был
        session = os.path.join(profiles.CONFIG_DIR, f".login_{admin_id}")
        client = TelegramClient(session, self.bot_cfg.api_id, self.bot_cfg.api_hash)
        await client.connect()
        self.wizards[admin_id] = Wizard(client)
        return "Пришли номер телефона аккаунта (с +)."

    async def feed_message(self, admin_id, text):
        wiz = self.wizards.get(admin_id)
        if wiz is None:
            return None
        try:
            if wiz.step == "phone":
                wiz.phone = text.strip()
                sent = await wiz.client.send_code_request(wiz.phone)
                wiz.phone_code_hash = sent.phone_code_hash
                wiz.step = "code"
                return ("Код отправлен в Telegram. Введи его <b>разбито</b> "
                        "(например <code>1 2 3 4 5</code>) — иначе Telegram его сожжёт.")
            if wiz.step == "code":
                code = extract_code(text)
                try:
                    await wiz.client.sign_in(phone=wiz.phone, code=code,
                                             phone_code_hash=wiz.phone_code_hash)
                except SessionPasswordNeededError:
                    wiz.step = "password"
                    return "У аккаунта включена 2FA. Пришли пароль облака."
                except (PhoneCodeInvalidError, PhoneCodeExpiredError):
                    return "Код неверный или истёк. Введи ещё раз (разбито)."
                return await self._finalize(admin_id, wiz)
            if wiz.step == "password":
                await wiz.client.sign_in(password=text.strip())
                return await self._finalize(admin_id, wiz)
        except FloodWaitError as e:
            await self.cancel(admin_id)
            return f"Telegram просит подождать {e.seconds} c. Попробуй позже."
        except PhoneNumberInvalidError:
            await self.cancel(admin_id)
            return "Неверный номер телефона. Начни заново кнопкой ➕."
        except Exception as e:
            await self.cancel(admin_id)
            log.warning("визард входа: %s", e)
            return f"Ошибка входа: {e}. Начни заново кнопкой ➕."
        return None

    async def _finalize(self, admin_id, wiz):
        me = await wiz.client.get_me()
        try:
            await wiz.client.disconnect()
        except Exception:
            pass
        self.wizards.pop(admin_id, None)
        name = f"id{me.id}"
        if name in self.accounts:
            await self.remove(name)  # тот же аккаунт уже есть — снять старый Capturer и его файлы
        env_path = profiles.write_profile_env(
            name, self.bot_cfg.api_id, self.bot_cfg.api_hash, me.id)
        profile = profiles.Profile(name, env_path)  # создаёт data_dir
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

    async def cancel(self, admin_id):
        wiz = self.wizards.pop(admin_id, None)
        if wiz is None:
            return False
        try:
            await wiz.client.disconnect()
        except Exception:
            pass
        session_file = os.path.join(profiles.CONFIG_DIR, f".login_{admin_id}.session")
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                os.remove(session_file + suffix)
            except OSError:
                pass
        return True
