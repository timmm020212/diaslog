"""Терминальный режим (нужен для ПЕРВОГО входа в аккаунт).

  python main.py            — аккаунт по умолчанию (.env)
  python main.py friend     — аккаунт friend (.env.friend)

После того как вход выполнен хотя бы раз (сессия сохранена в data/), все аккаунты
разом поднимаются раннером:  python run.py

Доставку делает общий бот (.env.bot). Если он уже настроен — перехваты идут и здесь.
"""
import os
import sys
import asyncio
import logging

from telethon import TelegramClient

import profiles
from store import Store
from capturer import Capturer

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("diaslog")


async def _run(profile):
    bot = None
    bot_cfg = profiles.load_bot()
    if bot_cfg and bot_cfg.configured:
        bot = TelegramClient(bot_cfg.session, bot_cfg.api_id, bot_cfg.api_hash)
        await bot.start(bot_token=bot_cfg.token)
    else:
        log.warning("Общий бот (.env.bot) не настроен — вход выполню, но доставки пока нет.")
    cap = Capturer(profile, Store, bot_client=bot)
    await cap.run_terminal()


def main():
    name = sys.argv[1].strip() if len(sys.argv) > 1 else "default"
    env_file = ".env" if name == "default" else f".env.{name}"
    # Читаем оттуда же, где run.py: локально — рядом с кодом, в облаке — с /data.
    env_path = os.path.join(profiles.CONFIG_DIR, env_file)
    if not os.path.exists(env_path):
        raise SystemExit(f"Нет файла {env_file} в {profiles.CONFIG_DIR}. "
                         "Создай его (см. DEPLOY.md / .env.example) и заполни ключи.")

    profile = profiles.Profile(name, env_path)
    if not profile.configured:
        raise SystemExit(f"В {env_file} не заполнены API_ID / API_HASH.")

    log.info("Аккаунт: %s", profile.label)
    try:
        asyncio.run(_run(profile))
    except KeyboardInterrupt:
        log.info("Остановлено пользователем.")


if __name__ == "__main__":
    main()
