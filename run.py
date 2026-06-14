"""Headless-раннер: поднимает ботов по всем профилям и работает 24/7.

Именно это запускается контейнером в облаке (CMD в Dockerfile). Веб-дашборда нет —
каждый бот шлёт перехваты (удалённые / изменённые / одноразовые) своему владельцу
прямо в Telegram.

Первый вход в аккаунт всё равно делается один раз через терминал:
  python main.py            — профиль по умолчанию (.env)
  python main.py friend     — профиль friend (.env.friend)
"""
import asyncio
import logging

import profiles
from store import Store
from capturer import Capturer

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("diaslog.run")


async def amain():
    found = profiles.discover()
    if not found:
        log.warning("Профилей нет (нет .env в %s). Создай .env / .env.friend, войди "
                    "(python main.py [friend]) и перезапусти.", profiles.CONFIG_DIR)
        await asyncio.Event().wait()
        return

    started = []
    for name, prof in found.items():
        if not prof.configured:
            log.warning("[%s] не настроен (нет API_ID / API_HASH / BOT_TOKEN) — пропускаю.", name)
            continue
        if not prof.session_exists:
            log.warning("[%s] нет сессии. Войди один раз: python main.py %s",
                        name, "" if name == "default" else name)
            continue
        cap = Capturer(prof, Store)
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
