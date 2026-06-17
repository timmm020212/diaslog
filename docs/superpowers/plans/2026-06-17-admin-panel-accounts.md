# Админ-панель аккаунтов — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать админу (`ADMIN_ID` из `.env.bot`) добавлять/удалять отслеживаемые аккаунты прямо из чата с ботом: визард входа (телефон → код → 2FA) и удаление, с сохранением на `/data` (переживают перезапуск).

**Architecture:** Новый модуль `admin.py` (`AccountManager` + визард) владеет жизненным циклом `Capturer`'ов и входом по телефону. `profiles.py` получает `ADMIN_ID` и хелперы записи/удаления динамических `.env.<id>`. `bot_ui.py` — экраны и кнопки админки. `run.py` создаёт `AccountManager`, маршрутизирует сообщения визарда и admin-callback'и, отдаёт реестр настроек из менеджера.

**Tech Stack:** Python 3.12, Telethon (вход по телефону + 2FA), python-dotenv, pytest.

---

## Структура файлов

- **Создать** `admin.py` — `AccountManager`, `Wizard`, `extract_code`, жизненный цикл и визард.
- **Создать** `tests/test_admin.py` — юнит-тест `extract_code`.
- **Создать** `tests/test_profiles_dynamic.py` — юнит-тесты хелперов профилей.
- **Изменить** `profiles.py` — `BotConfig.admin_id`, `env_path_for`, `write_profile_env`, `delete_profile`.
- **Изменить** `bot_ui.py` — admin-кнопки/тексты, `welcome_buttons(is_admin)`, `parse_admin`.
- **Изменить** `tests/test_bot_ui.py` — тесты `parse_admin` и admin-кнопок.
- **Изменить** `run.py` — `AccountManager`, обработчик сообщений визарда, admin-callback'и.
- **Изменить** `.env.bot.example`, `.env.bot` (локально), `DEPLOY.md`, `README.md` — `ADMIN_ID`.

---

## Task 1: profiles.py — ADMIN_ID и хелперы динамических аккаунтов

**Files:**
- Modify: `profiles.py`
- Test: `tests/test_profiles_dynamic.py`

- [ ] **Step 1: Написать падающие тесты**

Create `tests/test_profiles_dynamic.py`:

```python
import os

import profiles
from profiles import Profile


def test_botconfig_reads_admin_id(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "DATA_ROOT", str(tmp_path))
    p = tmp_path / ".env.bot"
    p.write_text("BOT_TOKEN=t\nAPI_ID=1\nAPI_HASH=h\nADMIN_ID=42\n", encoding="utf-8")
    cfg = profiles.BotConfig(str(p))
    assert cfg.admin_id == 42


def test_env_path_for_default_vs_dynamic(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "CONFIG_DIR", str(tmp_path))
    assert profiles.env_path_for("default").endswith(".env")
    assert profiles.env_path_for("id5").endswith(".env.id5")


def test_write_then_profile_reads_back(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(profiles, "CONFIG_DIR", str(tmp_path))
    path = profiles.write_profile_env("id777", 111, "hash777", 777)
    assert os.path.exists(path)
    pr = Profile("id777", path)
    assert pr.api_id == 111
    assert pr.api_hash == "hash777"
    assert pr.owner_id == 777


def test_delete_profile_removes_files(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(profiles, "CONFIG_DIR", str(tmp_path))
    path = profiles.write_profile_env("id9", 1, "h", 9)
    pr = Profile("id9", path)  # создаёт data_dir/media
    assert os.path.exists(pr.data_dir)
    profiles.delete_profile(pr)
    assert not os.path.exists(path)
    assert not os.path.exists(pr.data_dir)
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `python -m pytest tests/test_profiles_dynamic.py -v`
Expected: FAIL — `AttributeError: module 'profiles' has no attribute 'env_path_for'` (и т.п.).

- [ ] **Step 3: Добавить `admin_id` в `BotConfig`**

В `profiles.py`, в `BotConfig.__init__`, после строки
`self.api_hash = (vals.get("API_HASH") or "").strip()` добавить:

```python
        self.admin_id = _int(vals.get("ADMIN_ID"))
```

- [ ] **Step 4: Добавить хелперы в конце `profiles.py`**

Дописать в конец `profiles.py`:

```python


def env_path_for(name):
    """Путь к конфигу аккаунта: у default это .env, иначе .env.<name>."""
    fname = ".env" if name == "default" else f".env.{name}"
    return os.path.join(CONFIG_DIR, fname)


def write_profile_env(name, api_id, api_hash, owner_id,
                      cache_media=True, retention_days=7):
    """Записать конфиг динамически добавленного аккаунта на диск."""
    path = env_path_for(name)
    lines = [
        f"API_ID={api_id}",
        f"API_HASH={api_hash}",
        f"OWNER_ID={owner_id}",
        f"CACHE_MEDIA={'true' if cache_media else 'false'}",
        f"RETENTION_DAYS={retention_days}",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def delete_profile(profile):
    """Удалить конфиг аккаунта и его папку данных (сессия/кэш/медиа)."""
    import shutil
    try:
        os.remove(env_path_for(profile.name))
    except FileNotFoundError:
        pass
    shutil.rmtree(profile.data_dir, ignore_errors=True)
```

- [ ] **Step 5: Запустить — убедиться, что проходят**

Run: `python -m pytest tests/test_profiles_dynamic.py -v`
Expected: PASS — 4 passed.

- [ ] **Step 6: Коммит**

```bash
git add profiles.py tests/test_profiles_dynamic.py
git commit -m "profiles: ADMIN_ID и хелперы динамических аккаунтов (env_path_for/write/delete)"
```

---

## Task 2: bot_ui.py — презентация админ-панели

**Files:**
- Modify: `bot_ui.py`
- Test: `tests/test_bot_ui.py`

- [ ] **Step 1: Дописать падающие тесты**

В `tests/test_bot_ui.py` добавить в конец:

```python
def test_welcome_buttons_admin_flag():
    admin = [btn.text for row in bot_ui.welcome_buttons(is_admin=True) for btn in row]
    plain = [btn.text for row in bot_ui.welcome_buttons(is_admin=False) for btn in row]
    assert any("Админ" in t for t in admin)
    assert not any("Админ" in t for t in plain)


def test_parse_admin_actions():
    assert bot_ui.parse_admin(b"admin:open") == ("open", None)
    assert bot_ui.parse_admin(b"admin:add") == ("add", None)
    assert bot_ui.parse_admin(b"admin:remove") == ("remove", None)
    assert bot_ui.parse_admin(b"admin:cancel") == ("cancel", None)
    assert bot_ui.parse_admin(b"admin:rm:id5") == ("rm", "id5")
    assert bot_ui.parse_admin(b"admin:rmok:id5") == ("rmok", "id5")
    assert bot_ui.parse_admin(b"toggle:deleted") is None


def test_admin_text_lists_labels():
    text = bot_ui.admin_text(["timur", "Илья"])
    assert "Админ-панель" in text
    assert "timur" in text and "Илья" in text
```

- [ ] **Step 2: Запустить — убедиться, что падают**

Run: `python -m pytest tests/test_bot_ui.py -v`
Expected: FAIL — `AttributeError: module 'bot_ui' has no attribute 'parse_admin'` (и т.п.).

- [ ] **Step 3: Добавить admin-константы в `bot_ui.py`**

В `bot_ui.py`, сразу после блока `CB_TOGGLE = {...}` (перед `def parse_toggle`), добавить:

```python

# admin callback data
CB_ADMIN_OPEN = b"admin:open"
CB_ADMIN_ADD = b"admin:add"
CB_ADMIN_REMOVE = b"admin:remove"
CB_ADMIN_CANCEL = b"admin:cancel"
_ADMIN_RM_PREFIX = b"admin:rm:"
_ADMIN_RMOK_PREFIX = b"admin:rmok:"
_ADMIN_SIMPLE = {
    CB_ADMIN_OPEN: ("open", None),
    CB_ADMIN_ADD: ("add", None),
    CB_ADMIN_REMOVE: ("remove", None),
    CB_ADMIN_CANCEL: ("cancel", None),
}


def parse_admin(data):
    """Разбор admin-callback: (action, arg) или None.
    action ∈ open/add/remove/cancel/rm/rmok; arg — имя аккаунта для rm/rmok."""
    if data in _ADMIN_SIMPLE:
        return _ADMIN_SIMPLE[data]
    if data.startswith(_ADMIN_RMOK_PREFIX):
        return ("rmok", data[len(_ADMIN_RMOK_PREFIX):].decode())
    if data.startswith(_ADMIN_RM_PREFIX):
        return ("rm", data[len(_ADMIN_RM_PREFIX):].decode())
    return None
```

- [ ] **Step 4: Заменить `welcome_buttons` и добавить admin-экраны**

В `bot_ui.py` заменить функцию `welcome_buttons`:

```python
def welcome_buttons():
    return [[Button.inline("⚙️ Настройки", CB_OPEN)]]
```

на:

```python
def welcome_buttons(is_admin=False):
    rows = [[Button.inline("⚙️ Настройки", CB_OPEN)]]
    if is_admin:
        rows.append([Button.inline("🛠 Админ-панель", CB_ADMIN_OPEN)])
    return rows
```

И дописать в конец `bot_ui.py`:

```python


def admin_text(labels):
    body = "\n".join(f"• {l}" for l in labels) or "(пусто)"
    return (
        "🛠 <b>Админ-панель</b>\n"
        f"Аккаунтов под наблюдением: {len(labels)}\n\n"
        f"{body}"
    )


def admin_buttons():
    return [
        [Button.inline("➕ Добавить аккаунт", CB_ADMIN_ADD)],
        [Button.inline("➖ Удалить аккаунт", CB_ADMIN_REMOVE)],
        [Button.inline("◀️ Назад", CB_BACK)],
    ]


def remove_list_buttons(items):
    """items: [(name, label)] — кнопка на каждый аккаунт + Назад."""
    rows = [[Button.inline(f"➖ {label}", _ADMIN_RM_PREFIX + name.encode())]
            for name, label in items]
    rows.append([Button.inline("◀️ Назад", CB_ADMIN_OPEN)])
    return rows


def confirm_remove_buttons(name):
    return [
        [Button.inline("✅ Да, удалить", _ADMIN_RMOK_PREFIX + name.encode())],
        [Button.inline("◀️ Отмена", CB_ADMIN_OPEN)],
    ]


def wizard_cancel_buttons():
    return [[Button.inline("Отмена", CB_ADMIN_CANCEL)]]
```

- [ ] **Step 5: Запустить — убедиться, что проходят**

Run: `python -m pytest tests/test_bot_ui.py -v`
Expected: PASS — 8 passed (5 прежних + 3 новых).

- [ ] **Step 6: Коммит**

```bash
git add bot_ui.py tests/test_bot_ui.py
git commit -m "bot_ui: экраны и кнопки админ-панели, parse_admin, welcome для админа"
```

---

## Task 3: admin.py — AccountManager и визард входа

**Files:**
- Create: `admin.py`
- Test: `tests/test_admin.py`

Telethon-вход юнит-тестам не поддаётся; здесь TDD только `extract_code`, остальное — компиляция + ручная проверка (Task 6).

- [ ] **Step 1: Написать падающий тест**

Create `tests/test_admin.py`:

```python
from admin import extract_code


def test_extract_code_strips_non_digits():
    assert extract_code("1 2 3 4 5") == "12345"
    assert extract_code("a1b2c3") == "123"
    assert extract_code("код: 5-5-5-5-5") == "55555"
    assert extract_code("") == ""
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `python -m pytest tests/test_admin.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'admin'`.

- [ ] **Step 3: Создать `admin.py`**

Create `admin.py`:

```python
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
        env_path = profiles.write_profile_env(
            name, self.bot_cfg.api_id, self.bot_cfg.api_hash, me.id)
        profile = profiles.Profile(name, env_path)  # создаёт data_dir
        login_session = os.path.join(profiles.CONFIG_DIR, f".login_{admin_id}.session")
        try:
            os.replace(login_session, profile.user_session + ".session")
        except OSError as e:
            log.warning("перенос сессии: %s", e)
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
        return True
```

- [ ] **Step 4: Запустить тест и компиляцию**

Run: `python -m pytest tests/test_admin.py -v`
Expected: PASS — 1 passed.

Run: `python -m py_compile admin.py`
Expected: без ошибок.

- [ ] **Step 5: Коммит**

```bash
git add admin.py tests/test_admin.py
git commit -m "admin: AccountManager и визард входа по телефону (телефон/код/2FA)"
```

---

## Task 4: run.py — подключение AccountManager и маршрутизация

**Files:**
- Modify: `run.py`

- [ ] **Step 1: Обновить импорты**

В `run.py` заменить блок:

```python
import profiles
import bot_ui
from settings import Settings
from store import Store
from capturer import Capturer
```

на:

```python
import profiles
import bot_ui
from store import Store
from admin import AccountManager
```

(`Settings` и `Capturer` теперь используются внутри `AccountManager`, в `run.py` не нужны.)

- [ ] **Step 2: Заменить тело `amain` целиком**

Заменить всю функцию `amain` на:

```python
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

    manager = AccountManager(bot, bot_cfg, Store)

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
        try:
            await manager.start_profile(prof)
        except Exception as e:
            log.warning("[%s] не удалось запустить: %s", name, e)

    def is_admin(event):
        return bool(bot_cfg.admin_id) and event.sender_id == bot_cfg.admin_id

    async def show_settings(event, label, st):
        await event.edit(bot_ui.settings_text(label, st), parse_mode="html",
                         buttons=bot_ui.settings_buttons(st))

    async def handle_admin(event, action):
        kind, arg = action
        if kind == "open":
            await event.edit(bot_ui.admin_text(manager.labels()), parse_mode="html",
                             buttons=bot_ui.admin_buttons())
        elif kind == "add":
            prompt = await manager.begin_add(event.sender_id)
            await event.edit(prompt, parse_mode="html",
                             buttons=bot_ui.wizard_cancel_buttons())
        elif kind == "remove":
            await event.edit("Выбери аккаунт для удаления:",
                             buttons=bot_ui.remove_list_buttons(manager.list_items()))
        elif kind == "rm":
            cap = manager.accounts.get(arg)
            label = (cap.me_name if cap else None) or arg
            await event.edit(f"Точно удалить «{label}»? Сотрутся сессия и кэш.",
                             buttons=bot_ui.confirm_remove_buttons(arg))
        elif kind == "rmok":
            ok = await manager.remove(arg)
            await event.edit("✅ Аккаунт удалён." if ok else "Аккаунт не найден.",
                             buttons=bot_ui.admin_buttons())
        elif kind == "cancel":
            await manager.cancel(event.sender_id)
            await event.edit(bot_ui.WELCOME, parse_mode="html",
                             buttons=bot_ui.welcome_buttons(True))
        await event.answer()

    async def on_start(event):
        await event.respond(bot_ui.WELCOME, parse_mode="html",
                            buttons=bot_ui.welcome_buttons(is_admin(event)))

    async def on_callback(event):
        data = event.data
        if data == bot_ui.CB_BACK:
            await event.edit(bot_ui.WELCOME, parse_mode="html",
                             buttons=bot_ui.welcome_buttons(is_admin(event)))
            await event.answer()
            return
        action = bot_ui.parse_admin(data)
        if action is not None:
            if not is_admin(event):
                await event.answer("Нет доступа.", alert=True)
                return
            await handle_admin(event, action)
            return
        entry = manager.registry.get(event.sender_id)
        if entry is None:
            await event.answer("К тебе не привязан аккаунт.", alert=True)
            return
        label, st = entry
        if data == bot_ui.CB_OPEN:
            await show_settings(event, label, st)
            await event.answer()
            return
        key = bot_ui.parse_toggle(data)
        if key and key in bot_ui.CB_TOGGLE:
            st.toggle(key)
            await show_settings(event, label, st)
        await event.answer()

    async def on_message(event):
        if not is_admin(event):
            return
        if event.raw_text.startswith("/"):
            return
        if event.sender_id not in manager.wizards:
            return
        reply = await manager.feed_message(event.sender_id, event.raw_text)
        if reply:
            await event.respond(reply, parse_mode="html",
                                buttons=bot_ui.wizard_cancel_buttons())

    bot.add_event_handler(on_start, events.NewMessage(pattern="/start"))
    bot.add_event_handler(on_callback, events.CallbackQuery())
    bot.add_event_handler(on_message, events.NewMessage())

    if manager.accounts:
        log.info("Запущено аккаунтов: %d. Слежу за чатами, доставляю в Telegram.",
                 len(manager.accounts))
    else:
        log.warning("Ни один аккаунт не запущен (можно добавить через админ-панель).")
    await asyncio.Event().wait()  # держим цикл живым — на нём работают обработчики Telethon
```

- [ ] **Step 3: Компиляция всего проекта**

Run: `python -m py_compile run.py admin.py bot_ui.py settings.py capturer.py profiles.py main.py`
Expected: без ошибок.

- [ ] **Step 4: Все тесты**

Run: `python -m pytest -q`
Expected: PASS — 19 passed (6 settings + 8 bot_ui + 1 admin + 4 profiles_dynamic).

- [ ] **Step 5: Коммит**

```bash
git add run.py
git commit -m "run.py: подключить AccountManager, admin-callback'и и обработчик визарда"
```

---

## Task 5: ADMIN_ID в конфигах и доках

**Files:**
- Modify: `.env.bot.example`, `.env.bot` (локальный), `DEPLOY.md`, `README.md`

- [ ] **Step 1: `.env.bot.example`**

В `.env.bot.example` после блока `API_HASH=...` добавить:

```
# --- id админа (кому доступна админ-панель добавления/удаления аккаунтов) ---
# Узнать свой id: напиши боту /start. Пусто/0 = админ-панель выключена.
ADMIN_ID=0
```

- [ ] **Step 2: Локальный `.env.bot`**

В локальный `.env.bot` добавить строку (id владельца default-профиля):

```
ADMIN_ID=6140319824
```

- [ ] **Step 3: `DEPLOY.md` — упомянуть ADMIN_ID в блоке `.env.bot`**

В `DEPLOY.md`, в heredoc создания `.env.bot` (Шаг 5), добавить строку `ADMIN_ID=...` после `API_HASH=...`:

```sh
   cat > .env.bot <<'EOF'
   BOT_TOKEN=...
   API_ID=...
   API_HASH=...
   ADMIN_ID=...
   EOF
```

И одну поясняющую строку под блоком:

```
   > `ADMIN_ID` — твой Telegram id: тебе откроется «🛠 Админ-панель» в боте, где
   > можно добавлять/удалять отслеживаемые аккаунты (вход по телефону прямо в чате).
```

- [ ] **Step 4: `README.md` — короткий пункт про админ-панель**

В `README.md` в раздел «Настройка» (после пункта про `.env.bot`) добавить:

```markdown
4. (Опционально) В `.env.bot` укажи `ADMIN_ID` (твой Telegram id) — тогда в боте
   появится «🛠 Админ-панель»: добавляй/удаляй отслеживаемые аккаунты входом по
   телефону прямо в чате (код вводится «разбито»: `1 2 3 4 5`).
```

- [ ] **Step 5: Компиляция-санити и коммит**

Run: `python -m py_compile profiles.py`
Expected: без ошибок (правок кода нет, но проверим, что ничего не задето).

```bash
git add .env.bot.example DEPLOY.md README.md
git commit -m "Доки/пример: ADMIN_ID и админ-панель аккаунтов"
```

(Локальный `.env.bot` в `.gitignore` — не коммитим; он нужен только для локального запуска.)

---

## Task 6: Деплой и ручная сквозная проверка

**Files:** нет (проверка в Telegram).

- [ ] **Step 1: Запушить и пересобрать**

```bash
git push
```
В панели dockhost **Redeploy**. На `/data` в `.env.bot` добавить `ADMIN_ID=6140319824`
(через консоль контейнера: `printf 'ADMIN_ID=6140319824\n' >> /data/.env.bot`), затем
перезапустить контейнер.

- [ ] **Step 2: Кнопка админки видна только админу**

`/start` от твоего аккаунта → под приветствием есть «🛠 Админ-панель». От друга —
кнопки нет.

- [ ] **Step 3: Добавить аккаунт**

🛠 → ➕ Добавить аккаунт → пришли номер (с `+`) → бот просит код → введи код
**разбито** (`1 2 3 4 5`) → если 2FA, пришли пароль → ответ «✅ Аккаунт … добавлен».
В логах контейнера — `[idNNN] запущен как …`.

- [ ] **Step 4: Перехваты идут владельцу**

Владелец добавленного аккаунта жмёт `/start` боту. Пусть ему напишут в личку и удалят
сообщение → перехват приходит **владельцу**, не тебе.

- [ ] **Step 5: Удалить аккаунт**

🛠 → ➖ Удалить аккаунт → выбрать → подтвердить → «✅ Аккаунт удалён». Слежка
останавливается; в `/data` исчезают `.env.idNNN` и папка `idNNN`.

- [ ] **Step 6: Переживание перезапуска**

Добавь аккаунт, перезапусти контейнер → в логах снова `[idNNN] запущен как …`
(подхватился из `.env.idNNN`).

- [ ] **Step 7: Отмена визарда**

🛠 → ➕ → на любом шаге нажми «Отмена» → возврат к приветствию, визард сброшен.

---

## Самопроверка плана

- **Покрытие спеки:** ADMIN_ID → Task 1 (BotConfig) + Task 5; админ-кнопка только админу
  → Task 2 (`welcome_buttons`) + Task 4 (`is_admin`); экран панели → Task 2/4; визард
  телефон→код→2FA → Task 3 (`feed_message`); «разбитый» код → Task 3 (`extract_code`);
  финализация (get_me→owner, запись `.env.<id>`, перенос сессии, старт Capturer) →
  Task 3 (`_finalize`) + Task 1 (`write_profile_env`); удаление → Task 3 (`remove`) +
  Task 1 (`delete_profile`) + Task 4 (callback'и); хранение на `/data` и переживание
  перезапуска → Task 1 + существующий `discover()` (Task 6 Step 6); только-админ гейтинг
  → Task 4 (`is_admin`, «Нет доступа»). Пробелов нет.
- **Плейсхолдеров нет** — в каждом шаге полный код/команда.
- **Согласованность имён:** `BotConfig.admin_id`, `profiles.env_path_for/write_profile_env/
  delete_profile`, `bot_ui.welcome_buttons(is_admin)/admin_text/admin_buttons/
  remove_list_buttons/confirm_remove_buttons/wizard_cancel_buttons/parse_admin/
  CB_ADMIN_*`, `admin.AccountManager(.start_profile/.remove/.list_items/.labels/
  .begin_add/.feed_message/.cancel/.accounts/.registry/.wizards)`, `admin.extract_code`,
  `admin.Wizard` — имена совпадают во всех задачах.
