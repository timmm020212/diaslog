"""Headless-раннер: поднимает ОДИН общий бот и все аккаунты, работает 24/7.

Именно это запускается контейнером в облаке (CMD в Dockerfile). Веб-дашборда нет.
Один бот-доставщик (.env.bot) обслуживает все аккаунты: перехваты каждого аккаунта
(удалённые / изменённые / одноразовые) уходят владельцу этого аккаунта (OWNER_ID),
поэтому у разных людей в одном боте — только свои уведомления.

Первый вход в аккаунт всё равно делается один раз через терминал:
  python main.py            — аккаунт по умолчанию (.env)
  python main.py friend     — аккаунт friend (.env.friend)
"""
import asyncio
import logging

from telethon import TelegramClient, events
from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.types import BotCommand, BotCommandScopeDefault

import profiles
import bot_ui
from settings import Settings
from store import Store
from capturer import Capturer

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("diaslog.run")


async def amain():
    found = profiles.discover()
    bot_cfg = profiles.load_bot()

    if not found:
        log.warning("Аккаунтов нет (нет .env в %s). Создай .env / .env.friend, войди "
                    "(python main.py [friend]) и перезапусти.", profiles.CONFIG_DIR)
        await asyncio.Event().wait()
        return

    if not bot_cfg or not bot_cfg.configured:
        log.warning("Нет общего бота: создай %s/.env.bot с BOT_TOKEN / API_ID / API_HASH "
                    "и перезапусти.", profiles.CONFIG_DIR)
        await asyncio.Event().wait()
        return

    bot = TelegramClient(bot_cfg.session, bot_cfg.api_id, bot_cfg.api_hash)
    await bot.start(bot_token=bot_cfg.token)
    try:
        await bot(SetBotCommandsRequest(
            scope=BotCommandScopeDefault(), lang_code="",
            commands=[BotCommand("start", "Меню и настройки")]))
    except Exception as e:
        log.warning("Не выставить команды бота (кнопка «Меню»): %s", e)
    log.info("Общий бот-доставщик поднят.")

    registry = {}  # owner_id -> (label, Settings) — общий объект Settings с капчурером

    started = []
    for name, prof in found.items():
        if not prof.configured:
            log.warning("[%s] не настроен (нет API_ID / API_HASH) — пропускаю.", name)
            continue
        if not prof.owner_id:
            log.warning("[%s] нет OWNER_ID — некому доставлять, пропускаю.", name)
            continue
        if not prof.session_exists:
            log.warning("[%s] нет сессии. Войди один раз: python main.py %s",
                        name, "" if name == "default" else name)
            continue
        st = Settings.load(prof.settings_path)
        cap = Capturer(prof, Store, bot_client=bot, settings=st)
        try:
            await cap.start()
            started.append(cap)
            registry[prof.owner_id] = (cap.me_name or prof.label, st)
        except Exception as e:
            log.warning("[%s] не удалось запустить: %s", name, e)

    async def on_start(event):
        await event.respond(bot_ui.WELCOME, parse_mode="html",
                            buttons=bot_ui.welcome_buttons())

    async def show_settings(event, label, st):
        await event.edit(bot_ui.settings_text(label, st), parse_mode="html",
                         buttons=bot_ui.settings_buttons(st))

    async def on_callback(event):
        data = event.data
        if data == bot_ui.CB_BACK:
            await event.edit(bot_ui.WELCOME, parse_mode="html",
                             buttons=bot_ui.welcome_buttons())
            return
        entry = registry.get(event.sender_id)
        if entry is None:
            await event.answer("К тебе не привязан аккаунт.", alert=True)
            return
        label, st = entry
        if data == bot_ui.CB_OPEN:
            await show_settings(event, label, st)
            return
        key = bot_ui.parse_toggle(data)
        if key:
            st.toggle(key)
            await show_settings(event, label, st)
            await event.answer()

    bot.add_event_handler(on_start, events.NewMessage(pattern="/start"))
    bot.add_event_handler(on_callback, events.CallbackQuery())

    if started:
        log.info("Запущено аккаунтов: %d. Слежу за чатами, доставляю в Telegram.", len(started))
    else:
        log.warning("Ни один аккаунт не запущен. Поправь конфиги/сессии и перезапусти.")
    await asyncio.Event().wait()  # держим цикл живым — на нём работают обработчики Telethon


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        log.info("Остановлено пользователем.")
