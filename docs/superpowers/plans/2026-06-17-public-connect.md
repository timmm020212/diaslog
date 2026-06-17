# Публичная кнопка «Подключиться» + вход на выбор — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать любому пользователю кнопку «🔌 Подключиться» под приветствием с выбором способа входа — 📱 по коду (один телефон) или 🖥 по QR (второй экран); подключённый аккаунт попадает в систему, перехваты идут его владельцу.

**Architecture:** В `admin.py` визард становится двухрежимным (`begin_code` / `begin_qr`), `feed_message` ведёт шаги номер→код→2FA. `bot_ui` получает публичную кнопку и экран выбора способа. `run.py` обрабатывает публичные callback'и (connect / выбор / отмена) до админ-гейта и снимает `is_admin` в `on_message`.

**Tech Stack:** Python 3.12, Telethon (qr_login + код-вход), qrcode[pil], pytest.

---

## Структура файлов

- **Изменить** `admin.py` — двухрежимный визард (код/QR), вернуть `extract_code`, починить подпись QR.
- **Создать** `tests/test_admin.py` — тест `extract_code`.
- **Изменить** `bot_ui.py` — кнопка «Подключиться», экран выбора способа.
- **Изменить** `tests/test_bot_ui.py` — тесты кнопки и экрана выбора.
- **Изменить** `run.py` — публичные callback'и подключения/отмены, `add` → выбор способа, снять `is_admin` в `on_message`.

---

## Task 1: admin.py — двухрежимный визард (код + QR)

**Files:**
- Modify: `admin.py` (полная замена)
- Create: `tests/test_admin.py`

- [ ] **Step 1: Создать `tests/test_admin.py`:**

```python
from admin import extract_code


def test_extract_code_strips_non_digits():
    assert extract_code("1 2 3 4 5") == "12345"
    assert extract_code("a1b2c3") == "123"
    assert extract_code("") == ""
```

- [ ] **Step 2:** Run `python -m pytest tests/test_admin.py -v` — expect FAIL (`ImportError: cannot import name 'extract_code'`).

- [ ] **Step 3: Полностью заменить `admin.py` на:**

```python
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
                await wiz.client.sign_in(password=text.strip())
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
```

- [ ] **Step 4:** Run `python -m pytest tests/test_admin.py -v` — expect PASS (1 passed).

- [ ] **Step 5:** Run `python -m py_compile admin.py` — expect no errors. (`run.py` пока ссылается на старый `begin_add` — починим в Task 3; на компиляцию это не влияет.)

- [ ] **Step 6: Коммит**

```bash
git add admin.py tests/test_admin.py
git commit -m "admin: двухрежимный визард (код + QR), вернуть extract_code, чинить подпись QR"
```

---

## Task 2: bot_ui — кнопка «Подключиться» и экран выбора

**Files:**
- Modify: `bot_ui.py`
- Test: `tests/test_bot_ui.py`

- [ ] **Step 1: Дописать падающие тесты** в конец `tests/test_bot_ui.py`:

```python
def test_welcome_has_connect_button():
    labels = [b.text for row in bot_ui.welcome_buttons() for b in row]
    assert any("Подключиться" in t for t in labels)


def test_connect_method_buttons_have_code_and_qr():
    labels = [b.text for row in bot_ui.connect_method_buttons() for b in row]
    assert any("код" in t.lower() for t in labels)
    assert any("qr" in t.lower() for t in labels)
```

- [ ] **Step 2:** Run `python -m pytest tests/test_bot_ui.py -v` — expect FAIL (`AttributeError: ... 'connect_method_buttons'`).

- [ ] **Step 3: Добавить константы.** В `bot_ui.py`, сразу после строки `_ADMIN_RMOK_PREFIX = b"admin:rmok:"`, добавить:

```python
CB_CONNECT = b"connect"
CB_CONN_CODE = b"conn:code"
CB_CONN_QR = b"conn:qr"
```

- [ ] **Step 4: Заменить `welcome_buttons`.** Найти:
```python
def welcome_buttons(is_admin=False):
    rows = [[Button.inline("⚙️ Настройки", CB_OPEN)]]
    if is_admin:
        rows.append([Button.inline("🛠 Админ-панель", CB_ADMIN_OPEN)])
    return rows
```
Заменить на:
```python
def welcome_buttons(is_admin=False):
    rows = [
        [Button.inline("⚙️ Настройки", CB_OPEN)],
        [Button.inline("🔌 Подключиться", CB_CONNECT)],
    ]
    if is_admin:
        rows.append([Button.inline("🛠 Админ-панель", CB_ADMIN_OPEN)])
    return rows
```

- [ ] **Step 5: Добавить экран выбора** в конец `bot_ui.py`:

```python


def connect_method_text():
    return (
        "Как подключить аккаунт?\n\n"
        "📱 <b>По коду</b> — на одном телефоне (код придёт в твой Telegram).\n"
        "🖥 <b>По QR</b> — без кода, но нужен второй экран для сканирования."
    )


def connect_method_buttons():
    return [
        [Button.inline("📱 По коду", CB_CONN_CODE)],
        [Button.inline("🖥 По QR", CB_CONN_QR)],
        [Button.inline("◀️ Отмена", CB_ADMIN_CANCEL)],
    ]
```

- [ ] **Step 6:** Run `python -m pytest tests/test_bot_ui.py -v` — expect PASS (10 passed).

- [ ] **Step 7: Коммит**

```bash
git add bot_ui.py tests/test_bot_ui.py
git commit -m "bot_ui: публичная кнопка «Подключиться» и экран выбора код/QR"
```

---

## Task 3: run.py — публичные callback'и подключения

**Files:** Modify `run.py` (`on_callback`, `handle_admin`, `on_message`)

- [ ] **Step 1: Заменить `handle_admin`.** Найти ветки `add` и `cancel` внутри `handle_admin`:
```python
            elif kind == "add":
                await manager.begin_add(event.sender_id)
```
Заменить на (теперь «Добавить» ведёт в общий экран выбора способа):
```python
            elif kind == "add":
                await event.edit(bot_ui.connect_method_text(), parse_mode="html",
                                 buttons=bot_ui.connect_method_buttons())
```
И удалить целиком ветку `cancel` (отмена теперь обрабатывается публично в `on_callback`):
```python
            elif kind == "cancel":
                await manager.cancel(event.sender_id)
                await event.edit(bot_ui.WELCOME, parse_mode="html",
                                 buttons=bot_ui.welcome_buttons(True))
```

- [ ] **Step 2: Заменить `on_callback` целиком** на:

```python
    async def on_callback(event):
        data = event.data
        admin = is_admin(event)
        if data == bot_ui.CB_BACK:
            await event.edit(bot_ui.WELCOME, parse_mode="html",
                             buttons=bot_ui.welcome_buttons(admin))
            await _safe_answer(event)
            return
        # публичное подключение и отмена — доступно всем
        if data == bot_ui.CB_CONNECT:
            await event.edit(bot_ui.connect_method_text(), parse_mode="html",
                             buttons=bot_ui.connect_method_buttons())
            await _safe_answer(event)
            return
        if data == bot_ui.CB_CONN_CODE:
            reply = await manager.begin_code(event.sender_id)
            await event.respond(reply, parse_mode="html",
                                buttons=bot_ui.wizard_cancel_buttons())
            await _safe_answer(event)
            return
        if data == bot_ui.CB_CONN_QR:
            await manager.begin_qr(event.sender_id)
            await _safe_answer(event)
            return
        if data == bot_ui.CB_ADMIN_CANCEL:
            await manager.cancel(event.sender_id)
            await event.edit(bot_ui.WELCOME, parse_mode="html",
                             buttons=bot_ui.welcome_buttons(admin))
            await _safe_answer(event)
            return
        action = bot_ui.parse_admin(data)
        if action is not None:
            if not admin:
                await _safe_answer(event, "Нет доступа.", alert=True)
                return
            await handle_admin(event, action)
            return
        entry = manager.registry.get(event.sender_id)
        if entry is None:
            await _safe_answer(event, "К тебе не привязан аккаунт.", alert=True)
            return
        label, st = entry
        if data == bot_ui.CB_OPEN:
            await show_settings(event, label, st)
            await _safe_answer(event)
            return
        key = bot_ui.parse_toggle(data)
        if key and key in bot_ui.CB_TOGGLE:
            st.toggle(key)
            await show_settings(event, label, st)
        await _safe_answer(event)
```

- [ ] **Step 3: Заменить `on_message` целиком** на (снят гейт `is_admin`; кнопки после визарда — отмена пока идёт, иначе без кнопок):

```python
    async def on_message(event):
        if event.raw_text.startswith("/"):
            return
        if event.sender_id not in manager.wizards:
            return
        reply = await manager.feed_message(event.sender_id, event.raw_text)
        if reply:
            still = event.sender_id in manager.wizards
            buttons = bot_ui.wizard_cancel_buttons() if still else None
            await event.respond(reply, parse_mode="html", buttons=buttons)
```

- [ ] **Step 4: Компиляция всего проекта**

Run: `python -m py_compile run.py admin.py bot_ui.py settings.py capturer.py profiles.py main.py`
Expected: без ошибок.

- [ ] **Step 5: Все тесты**

Run: `python -m pytest -q`
Expected: PASS — 21 passed (6 settings + 10 bot_ui + 1 admin + 4 profiles_dynamic).

- [ ] **Step 6: Коммит**

```bash
git add run.py
git commit -m "run.py: публичные кнопки подключения (код/QR/отмена), add → выбор способа, on_message без гейта"
```

---

## Task 4: Деплой и ручная сквозная проверка

**Files:** нет (проверка в Telegram).

- [ ] **Step 1: Запушить и пересобрать**

```bash
git push
```
В панели dockhost **Redeploy**, дождись `Общий бот-доставщик поднят.`

- [ ] **Step 2: Кнопка видна всем**

`/start` → под приветствием **🔌 Подключиться** (видна и у тебя, и у не-админа).

- [ ] **Step 3: Подключение по коду (один телефон)**

🔌 Подключиться → **📱 По коду** → пришли номер (с +) → код придёт в чат «Telegram»
этого аккаунта → введи **разбито** (`1 2 3 4 5`) → [2FA пароль] → `✅ Аккаунт подключён`.
В логах `[idNNN] запущен как …`.

- [ ] **Step 4: Подключение по QR (второй экран)**

🔌 Подключиться → **🖥 По QR** → открой этот чат на компе (web.telegram.org) → на
телефоне аккаунта Настройки → Устройства → Подключить устройство → отсканируй QR →
подтверди → `✅ Аккаунт подключён`.

- [ ] **Step 5: Перехваты и доступ не-админа**

Перехваты подключённого аккаунта приходят владельцу (в этот чат). Проверь, что
не-админ тоже видит «🔌 Подключиться» и может подключиться, но НЕ видит «🛠 Админ-панель».

- [ ] **Step 6: Отмена и перезапуск**

«Отмена» в процессе → возврат к приветствию. Перезапусти контейнер → подключённый
аккаунт снова в логах (`.env.idNNN`).

---

## Самопроверка плана

- **Покрытие спеки:** публичная кнопка → Task 2 (`welcome_buttons`) + Task 3 (`CB_CONNECT`);
  экран выбора код/QR → Task 2 (`connect_method_*`) + Task 3 (`conn:code`/`conn:qr`);
  вход по коду (номер→код→2FA) → Task 1 (`begin_code`/`feed_message`/`extract_code`);
  вход по QR → Task 1 (`begin_qr`/`_qr_loop`); подпись QR без ссылки → Task 1
  (`_qr_caption`); владелец = аккаунт → Task 1 (`_finalize`); снятие гейта → Task 3
  (`on_message`); отмена публично → Task 3 (`CB_ADMIN_CANCEL` до админ-гейта); admin
  add → выбор способа → Task 3 (`handle_admin`). Пробелов нет.
- **Плейсхолдеров нет** — полный код в каждом шаге.
- **Согласованность имён:** `AccountManager.begin_code/begin_qr/feed_message/_finalize/
  _cleanup/cancel/_new_client/_qr_png/_qr_caption/_qr_loop`, `Wizard(client, mode)` с
  `mode/step/phone/phone_code_hash/qr/qr_msg/qr_task`, `extract_code`, `bot_ui.CB_CONNECT/
  CB_CONN_CODE/CB_CONN_QR/connect_method_text/connect_method_buttons/welcome_buttons/
  wizard_cancel_buttons/CB_ADMIN_CANCEL` — совпадают во всех задачах. `run.py` зовёт
  `manager.begin_code/begin_qr/cancel/feed_message` (Task 1) — сигнатуры совпадают.
