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

import profiles
from store import Store
from capturer import Capturer

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("diaslog.run")

WELCOME = (
    "\U0001F575 Привет! Это <b>DIASLOG INTERCEPT</b>.\n\n"
    "Я приношу тебе то, что пытаются скрыть в Telegram:\n"
    "\U0001F5D1 удалённые сообщения\n"
    "✏️ изменённые сообщения\n"
    "\U0001F441 одноразовые фото и видео"
)


async def _on_start(event):
    await event.reply(WELCOME, parse_mode="html")


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
    bot.add_event_handler(_on_start, events.NewMessage(pattern="/start"))
    await bot.start(bot_token=bot_cfg.token)
    log.info("Общий бот-доставщик поднят.")

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
        cap = Capturer(prof, Store, bot_client=bot)
        try:
            await cap.start()
            started.append(cap)
        except Exception as e:
            log.warning("[%s] не удалось запустить: %s", name, e)

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
