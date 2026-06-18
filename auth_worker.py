"""Локальный воркер авторизации — запускается на ДОМАШНЕМ ПК (обычный IP).

Делает реальный вход по коду (send_code_request / sign_in) с твоего IP, поэтому
код Telegram доставляет нормально. Готовую сессию отдаёт боту на сервере, который
её сохраняет и запускает слежение. Пользователь при этом всё делает в боте.

Запуск (ПК должен быть онлайн, пока кто-то подключает аккаунт):
    python auth_worker.py

Конфиг — рядом в .env.worker (см. .env.worker.example):
    RELAY_URL=https://твой-контейнер.dockhost.ru
    RELAY_TOKEN=тот_же_секрет_что_в_.env.bot
    RELAY_POLL=2        # сек между опросами (необязательно)
"""
import asyncio
import logging
import os
import sys

import aiohttp
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (SessionPasswordNeededError, PhoneCodeInvalidError,
                             PhoneCodeExpiredError, FloodWaitError)

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("diaslog.worker")

# job_id -> (TelegramClient, phone_code_hash) — держим клиента живым между шагами
CLIENTS = {}


def load_env():
    """Подтянуть .env.worker рядом со скриптом в окружение (без зависимостей)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.worker")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


async def post(session, base, token, path, payload):
    async with session.post(f"{base}{path}", json=payload,
                            headers={"Authorization": f"Bearer {token}"}) as r:
        return await r.json()


async def _disconnect(job_id):
    entry = CLIENTS.pop(job_id, None)
    if entry:
        try:
            await entry[0].disconnect()
        except Exception:  # noqa: BLE001
            pass


async def _finish(session, base, token, job):
    """Успешный вход: выгрузить строку сессии и отдать серверу."""
    client, _ = CLIENTS.pop(job["id"])
    me = await client.get_me()
    string = client.session.save()
    try:
        await client.disconnect()
    except Exception:  # noqa: BLE001
        pass
    await post(session, base, token, f"/jobs/{job['id']}/session",
               {"session": string, "me_id": me.id, "me_name": me.first_name or ""})
    log.info("аккаунт %s (id %s) авторизован, сессия отправлена", me.first_name, me.id)


async def handle_phone(session, base, token, job):
    client = TelegramClient(StringSession(), int(job["api_id"]), job["api_hash"])
    await client.connect()
    try:
        sent = await client.send_code_request(job["phone"])
    except FloodWaitError as e:
        await client.disconnect()
        await post(session, base, token, f"/jobs/{job['id']}/status",
                   {"status": "error", "error": f"Telegram просит подождать {e.seconds} c"})
        return
    except Exception as e:  # noqa: BLE001
        await client.disconnect()
        await post(session, base, token, f"/jobs/{job['id']}/status",
                   {"status": "error", "error": str(e)})
        return
    CLIENTS[job["id"]] = (client, sent.phone_code_hash)
    await post(session, base, token, f"/jobs/{job['id']}/status", {"status": "code_sent"})
    log.info("код запрошен для %s", job["phone"])


async def handle_code(session, base, token, job):
    entry = CLIENTS.get(job["id"])
    if not entry:
        await post(session, base, token, f"/jobs/{job['id']}/status",
                   {"status": "error", "error": "сессия воркера потеряна, начни заново"})
        return
    client, phash = entry
    try:
        await client.sign_in(phone=job["phone"], code=job["code"], phone_code_hash=phash)
    except SessionPasswordNeededError:
        await post(session, base, token, f"/jobs/{job['id']}/status",
                   {"status": "need_password"})
        return
    except (PhoneCodeInvalidError, PhoneCodeExpiredError):
        await post(session, base, token, f"/jobs/{job['id']}/status",
                   {"status": "error", "error": "код неверный или истёк"})
        await _disconnect(job["id"])
        return
    except Exception as e:  # noqa: BLE001
        await post(session, base, token, f"/jobs/{job['id']}/status",
                   {"status": "error", "error": str(e)})
        await _disconnect(job["id"])
        return
    await _finish(session, base, token, job)


async def handle_password(session, base, token, job):
    entry = CLIENTS.get(job["id"])
    if not entry:
        await post(session, base, token, f"/jobs/{job['id']}/status",
                   {"status": "error", "error": "сессия воркера потеряна, начни заново"})
        return
    client, _ = entry
    try:
        await client.sign_in(password=job["password"])
    except Exception as e:  # noqa: BLE001
        await post(session, base, token, f"/jobs/{job['id']}/status",
                   {"status": "error", "error": f"пароль не подошёл: {e}"})
        await _disconnect(job["id"])
        return
    await _finish(session, base, token, job)


HANDLERS = {
    "phone_submitted": handle_phone,
    "code_submitted": handle_code,
    "password_submitted": handle_password,
}


async def main():
    load_env()
    base = os.getenv("RELAY_URL", "").rstrip("/")
    token = os.getenv("RELAY_TOKEN", "")
    poll = float(os.getenv("RELAY_POLL", "2"))
    if not base or not token:
        sys.exit("Задай RELAY_URL и RELAY_TOKEN (в .env.worker рядом со скриптом).")

    log.info("Воркер запущен. Опрашиваю %s каждые %.0f c. Не закрывай это окно.", base, poll)
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(f"{base}/jobs",
                        headers={"Authorization": f"Bearer {token}"}) as r:
                    data = await r.json()
                for job in data.get("jobs", []):
                    handler = HANDLERS.get(job.get("status"))
                    if handler:
                        await handler(session, base, token, job)
            except Exception as e:  # noqa: BLE001
                log.warning("опрос не удался: %s", e)
            await asyncio.sleep(poll)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Воркер остановлен.")
