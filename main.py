"""Терминальный режим (нужен для ПЕРВОГО входа в аккаунт).

  python main.py            — профиль по умолчанию (.env)
  python main.py friend     — профиль friend (.env.friend)

После того как вход выполнен хотя бы раз (сессия сохранена в data/), все аккаунты
разом поднимаются раннером:  python run.py
"""
import os
import sys
import asyncio
import logging

import profiles
from store import Store
from capturer import Capturer

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("diaslog")


def main():
    name = sys.argv[1].strip() if len(sys.argv) > 1 else "default"
    env_file = ".env" if name == "default" else f".env.{name}"
    env_path = os.path.join(profiles.BASE_DIR, env_file)
    if not os.path.exists(env_path):
        raise SystemExit(f"Нет файла {env_file}. Скопируй пример и заполни ключи.")

    profile = profiles.Profile(name, env_path)
    if not profile.configured:
        raise SystemExit(f"В {env_file} не заполнены API_ID / API_HASH / BOT_TOKEN.")

    cap = Capturer(profile, Store)
    log.info("Профиль: %s", profile.label)
    try:
        asyncio.run(cap.run_terminal())
    except KeyboardInterrupt:
        log.info("Остановлено пользователем.")


if __name__ == "__main__":
    main()
