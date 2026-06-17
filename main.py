"""Терминальный режим — нужен ТОЛЬКО для первого входа в аккаунт.

  python main.py            — аккаунт по умолчанию (.env)
  python main.py friend     — аккаунт friend (.env.friend)

Здесь поднимается только юзербот-клиент: задача — войти и сохранить сессию аккаунта.
Бота-доставщика тут НЕ запускаем: им владеет run.py (24/7), и общая сессия бота не
должна одновременно открываться двумя процессами. После входа подними всё через run.py.
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
    # Читаем оттуда же, где run.py: локально — рядом с кодом, в облаке — с /data.
    env_path = os.path.join(profiles.CONFIG_DIR, env_file)
    if not os.path.exists(env_path):
        raise SystemExit(f"Нет файла {env_file} в {profiles.CONFIG_DIR}. "
                         "Создай его (см. DEPLOY.md / .env.example) и заполни ключи.")

    profile = profiles.Profile(name, env_path)
    if not profile.configured:
        raise SystemExit(f"В {env_file} не заполнены API_ID / API_HASH.")

    log.info("Аккаунт: %s", profile.label)
    cap = Capturer(profile, Store)  # без бота: только вход и сохранение сессии аккаунта
    try:
        asyncio.run(cap.run_terminal())
    except KeyboardInterrupt:
        log.info("Остановлено пользователем.")


if __name__ == "__main__":
    main()
