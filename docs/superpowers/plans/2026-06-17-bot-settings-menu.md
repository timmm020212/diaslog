# Меню бота и настройки фильтрации — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать каждому владельцу через общего бота кнопку «Меню» → `/start` → приветствие + ⚙️ Настройки, где он включает/выключает бота и выбирает, какие перехваты получать (удалённые / изменённые / одноразовые), по принципу «не кэшировать отключённое».

**Architecture:** Состояние выносится в новый модуль `settings.py` (`Settings`: поля + предикаты + load/save JSON на `/data`). Презентация (тексты, инлайн-клавиатуры) — в новый модуль `bot_ui.py`. `capturer.py` сверяется с предикатами `Settings` в обработчиках (каскадный гейтинг). `run.py` поднимает бота, выставляет команды, держит реестр `{owner_id: (label, Settings)}` и обрабатывает нажатия кнопок; один объект `Settings` общий у капчурера и обработчика → изменения мгновенны.

**Tech Stack:** Python 3.12, Telethon, python-dotenv, pytest (только для локальных тестов; в runtime-образ не идёт).

---

## Структура файлов

- **Создать** `settings.py` — класс `Settings` (состояние + предикаты + персистентность).
- **Создать** `bot_ui.py` — тексты приветствия/настроек, инлайн-клавиатуры, разбор callback.
- **Создать** `tests/test_settings.py` — юнит-тесты `Settings`.
- **Создать** `tests/test_bot_ui.py` — юнит-тесты текста настроек и разбора callback.
- **Изменить** `profiles.py` — добавить `Profile.settings_path`.
- **Изменить** `capturer.py` — принять `settings`, гейтить `_on_new` / `_on_deleted` / `_on_edited`.
- **Изменить** `run.py` — команды бота, реестр настроек, `/start` с кнопкой, обработчик `CallbackQuery`.

---

## Task 1: Модуль Settings (состояние + предикаты)

**Files:**
- Create: `settings.py`
- Test: `tests/test_settings.py`

- [ ] **Step 1: Установить pytest (один раз, локально)**

Run: `pip install pytest`
Expected: `Successfully installed pytest-...` (или «already satisfied»).

- [ ] **Step 2: Написать падающие тесты**

Create `tests/test_settings.py`:

```python
import os

from settings import Settings


def test_defaults_all_on(tmp_path):
    s = Settings(str(tmp_path / "settings.json"))
    assert s.enabled and s.deleted and s.edited and s.view_once
    assert s.wants_deleted() and s.wants_edited() and s.wants_view_once()
    assert s.cache_incoming()


def test_disabled_master_blocks_everything(tmp_path):
    s = Settings(str(tmp_path / "settings.json"))
    s.toggle("enabled")
    assert s.enabled is False
    assert not s.wants_deleted()
    assert not s.wants_edited()
    assert not s.wants_view_once()
    assert not s.cache_incoming()


def test_cache_incoming_needs_deleted_or_edited(tmp_path):
    s = Settings(str(tmp_path / "settings.json"))
    s.toggle("deleted")          # deleted off, edited on
    assert s.cache_incoming()    # ещё нужно для изменённых
    assert not s.wants_deleted()
    s.toggle("edited")           # оба off
    assert not s.cache_incoming()


def test_save_load_roundtrip(tmp_path):
    path = str(tmp_path / "settings.json")
    s = Settings(path)
    s.toggle("view_once")        # выключаем одноразовые и сохраняем
    s2 = Settings.load(path)
    assert s2.view_once is False
    assert s2.deleted is True


def test_load_missing_file_returns_defaults(tmp_path):
    s = Settings.load(str(tmp_path / "nope.json"))
    assert s.enabled and s.deleted and s.edited and s.view_once


def test_toggle_unknown_key_raises(tmp_path):
    s = Settings(str(tmp_path / "settings.json"))
    try:
        s.toggle("nonsense")
        assert False, "ожидали KeyError"
    except KeyError:
        pass
```

- [ ] **Step 3: Запустить тесты — убедиться, что падают**

Run: `python -m pytest tests/test_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'settings'`.

- [ ] **Step 4: Реализовать `settings.py`**

Create `settings.py`:

```python
"""Настройки фильтрации перехвата — на каждый аккаунт отдельно.

Хранятся в data_dir/settings.json на диске /data. Определяют, что присылать:
удалённые / изменённые / одноразовые, плюс общий выключатель enabled.
По умолчанию всё включено (поведение как раньше).

Предикаты (wants_* / cache_incoming) — единственный источник правды о гейтинге,
их использует capturer. Хранение и предикаты держим вместе, презентацию — в bot_ui.
"""
import json
import os

FIELDS = ("enabled", "deleted", "edited", "view_once")


class Settings:
    def __init__(self, path, enabled=True, deleted=True, edited=True, view_once=True):
        self.path = path
        self.enabled = enabled
        self.deleted = deleted
        self.edited = edited
        self.view_once = view_once

    @classmethod
    def load(cls, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, ValueError):
            data = {}
        return cls(
            path,
            enabled=bool(data.get("enabled", True)),
            deleted=bool(data.get("deleted", True)),
            edited=bool(data.get("edited", True)),
            view_once=bool(data.get("view_once", True)),
        )

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: getattr(self, k) for k in FIELDS}, f)
        os.replace(tmp, self.path)  # атомарная замена

    def toggle(self, key):
        if key not in FIELDS:
            raise KeyError(key)
        setattr(self, key, not getattr(self, key))
        self.save()
        return getattr(self, key)

    # ---------- предикаты гейтинга (используются capturer) ----------
    def wants_deleted(self):
        return self.enabled and self.deleted

    def wants_edited(self):
        return self.enabled and self.edited

    def wants_view_once(self):
        return self.enabled and self.view_once

    def cache_incoming(self):
        """Сохранять ли входящие в кэш (нужно и для удалённых, и для изменённых)."""
        return self.enabled and (self.deleted or self.edited)
```

- [ ] **Step 5: Запустить тесты — убедиться, что проходят**

Run: `python -m pytest tests/test_settings.py -v`
Expected: PASS — 6 passed.

- [ ] **Step 6: Коммит**

```bash
git add settings.py tests/test_settings.py
git commit -m "Settings: состояние фильтров и предикаты гейтинга"
```

---

## Task 2: Путь к файлу настроек в Profile

**Files:**
- Modify: `profiles.py` (метод `Profile.__init__`)

- [ ] **Step 1: Добавить `settings_path`**

В `profiles.py` в `Profile.__init__`, сразу после строки
`self.user_session = os.path.join(self.data_dir, "user_session")`, добавить:

```python
        self.settings_path = os.path.join(self.data_dir, "settings.json")
```

- [ ] **Step 2: Проверить, что профили читаются**

Run: `python -c "import profiles; p=profiles.Profile('default', '.env'); print(p.settings_path)"`
Expected: путь оканчивается на `data/settings.json` (или `data\settings.json` на Windows).

- [ ] **Step 3: Коммит**

```bash
git add profiles.py
git commit -m "Profile: путь к settings.json в data_dir"
```

---

## Task 3: Презентация бота (bot_ui)

**Files:**
- Create: `bot_ui.py`
- Test: `tests/test_bot_ui.py`

- [ ] **Step 1: Написать падающие тесты**

Create `tests/test_bot_ui.py`:

```python
import bot_ui
from settings import Settings


def _settings(**kw):
    s = Settings("/tmp/x.json")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_settings_text_enabled_shows_green():
    text = bot_ui.settings_text("timur", _settings())
    assert "timur" in text
    assert "🟢" in text
    assert "🔴" not in text


def test_settings_text_disabled_shows_red():
    text = bot_ui.settings_text("timur", _settings(enabled=False))
    assert "🔴" in text


def test_parse_toggle_extracts_key():
    assert bot_ui.parse_toggle(b"toggle:deleted") == "deleted"
    assert bot_ui.parse_toggle(b"toggle:view_once") == "view_once"


def test_parse_toggle_returns_none_for_others():
    assert bot_ui.parse_toggle(b"open_settings") is None
    assert bot_ui.parse_toggle(b"back") is None
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `python -m pytest tests/test_bot_ui.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot_ui'`.

- [ ] **Step 3: Реализовать `bot_ui.py`**

Create `bot_ui.py`:

```python
"""Тексты и инлайн-клавиатуры бота (приветствие + настройки).

Презентация отделена от оркестрации (run.py) и состояния (settings.py),
чтобы тексты и разбор callback можно было проверять без Telegram.
"""
from telethon import Button

WELCOME = (
    "\U0001F575 Привет! Это <b>DIASLOG INTERCEPT</b>.\n\n"
    "Я приношу тебе то, что пытаются скрыть в Telegram:\n"
    "\U0001F5D1 удалённые сообщения\n"
    "✏️ изменённые сообщения\n"
    "\U0001F441 одноразовые фото и видео"
)

# callback data (bytes) для инлайн-кнопок
CB_OPEN = b"open_settings"
CB_BACK = b"back"
_TOGGLE_PREFIX = b"toggle:"
CB_TOGGLE = {
    "enabled": _TOGGLE_PREFIX + b"enabled",
    "deleted": _TOGGLE_PREFIX + b"deleted",
    "edited": _TOGGLE_PREFIX + b"edited",
    "view_once": _TOGGLE_PREFIX + b"view_once",
}


def parse_toggle(data):
    """b'toggle:deleted' -> 'deleted'; для прочего -> None."""
    if data.startswith(_TOGGLE_PREFIX):
        return data[len(_TOGGLE_PREFIX):].decode()
    return None


def welcome_buttons():
    return [[Button.inline("⚙️ Настройки", CB_OPEN)]]


def _mark(on):
    return "✅" if on else "❌"


def settings_text(label, s):
    head = "🟢 Бот включён" if s.enabled else "🔴 Бот выключен — перехватов нет"
    return (
        f"⚙️ <b>Настройки</b> — аккаунт «{label}»\n\n"
        f"{head}\n\n"
        "Что присылать при перехвате:"
    )


def settings_buttons(s):
    master = "🔴 Выключить бота" if s.enabled else "🟢 Включить бота"
    return [
        [Button.inline(master, CB_TOGGLE["enabled"])],
        [Button.inline(f"🗑 Удалённые {_mark(s.deleted)}", CB_TOGGLE["deleted"]),
         Button.inline(f"✏️ Изменённые {_mark(s.edited)}", CB_TOGGLE["edited"])],
        [Button.inline(f"👁 Одноразовые {_mark(s.view_once)}", CB_TOGGLE["view_once"])],
        [Button.inline("◀️ Назад", CB_BACK)],
    ]
```

- [ ] **Step 4: Запустить тесты — убедиться, что проходят**

Run: `python -m pytest tests/test_bot_ui.py -v`
Expected: PASS — 4 passed.

- [ ] **Step 5: Коммит**

```bash
git add bot_ui.py tests/test_bot_ui.py
git commit -m "bot_ui: тексты приветствия/настроек и инлайн-клавиатуры"
```

---

## Task 4: Гейтинг захвата по настройкам (capturer)

**Files:**
- Modify: `capturer.py` (`__init__`, `_on_new`, `_on_deleted`, `_on_edited`, `start`)

Юнит-тестам Telethon-обработчики не поддаются; логика гейтинга уже покрыта предикатами в Task 1. Здесь — подключение предикатов + компиляция + ручная проверка в Task 6.

- [ ] **Step 1: Принять `settings` в конструкторе**

В `capturer.py` заменить сигнатуру и тело `__init__`. Найти:

```python
    def __init__(self, profile, store_factory, bot_client=None):
        self.profile = profile
        self._store_factory = store_factory  # вызывается в потоке с циклом asyncio
        self.bot_client = bot_client  # общий бот-доставщик (один на всех); None при входе
        self.store = None
```

Заменить на:

```python
    def __init__(self, profile, store_factory, bot_client=None, settings=None):
        self.profile = profile
        self._store_factory = store_factory  # вызывается в потоке с циклом asyncio
        self.bot_client = bot_client  # общий бот-доставщик (один на всех); None при входе
        # Настройки фильтрации (общий объект с обработчиком кнопок в run.py).
        # None (терминальный вход) = всё включено по умолчанию.
        self.settings = settings or Settings(path=None)
        self.store = None
```

- [ ] **Step 2: Импортировать Settings**

В `capturer.py` в блоке импортов после `import util` добавить:

```python
from settings import Settings
```

- [ ] **Step 3: Добавить общий список медиа-типов**

В `capturer.py` сразу после строки `log = logging.getLogger("diaslog.capturer")` добавить:

```python

# Медиа, которые качаем у входящих, чтобы прислать удалённый файл.
CACHEABLE_MEDIA = ("photo", "video", "voice", "video_note", "document", "gif", "sticker")
```

- [ ] **Step 4: Гейтинг `_on_new`**

Заменить весь метод `_on_new` целиком на:

```python
    async def _on_new(self, event):
        if not self.settings.enabled:
            return
        if not (event.is_private or event.is_group):
            return
        msg = event.message
        sender = await event.get_sender()
        sname = util.real_name(sender)
        uname = util.username_of(sender)
        kind = util.media_kind(msg)

        if util.is_view_once(msg):
            if not (self.settings.wants_view_once() and event.is_private):
                return  # одноразовые выключены или это группа — не трогаем
            media_path = await self._download(msg)
            head = (f"\U0001F441 {util.mention_html(sname, uname)} прислал(а) "
                    f"одноразовое {util.media_label(kind)} \U0001F525")
            caption = head + (f"\n\n{self._quote(msg.message)}" if msg.message else "")
            if media_path:
                await self._send_media(media_path, caption)
            else:
                await self._send_text(head + "\n(не удалось скачать ⚠️)")
            return  # одноразовое эфемерно — в кэш не кладём

        if not self.settings.cache_incoming():
            return  # ни удалённые, ни изменённые не нужны — не сохраняем

        chat_title = None
        if event.is_group:
            chat = await event.get_chat()
            chat_title = getattr(chat, "title", None)

        media_path = None
        if self.settings.wants_deleted() and self.profile.cache_media and kind in CACHEABLE_MEDIA:
            media_path = await self._download(msg)

        self.store.save_message(
            chat_id=event.chat_id, msg_id=msg.id,
            sender_id=getattr(sender, "id", None), sender_name=sname,
            sender_username=uname, chat_title=chat_title, text=msg.message or "",
            media_path=media_path, media_type=kind, date=str(msg.date),
        )
```

- [ ] **Step 5: Гейтинг `_on_deleted`**

В методе `_on_deleted` первой строкой тела (перед `chat_id = event.chat_id`) добавить:

```python
        if not self.settings.wants_deleted():
            return
```

- [ ] **Step 6: Гейтинг `_on_edited`**

Заменить весь метод `_on_edited` целиком на:

```python
    async def _on_edited(self, event):
        if not self.settings.enabled:
            return
        if not (event.is_private or event.is_group):
            return
        msg = event.message
        new_text = msg.message or ""
        row = self.store.get_message(event.chat_id, msg.id)
        if row is not None:
            old_text = row["text"] or ""
            if old_text != new_text:
                if self.settings.wants_edited():
                    who = util.mention_html(row["sender_name"], row["sender_username"])
                    where = f" в «{util.html_escape(row['chat_title'])}»" if row["chat_title"] else ""
                    await self._send_text(
                        f"✏️ {who} изменил(а) сообщение{where}\n\n"
                        f"Было:\n{self._quote(old_text)}\n"
                        f"Стало:\n{self._quote(new_text)}"
                    )
                self.store.update_text(event.chat_id, msg.id, new_text)  # держим кэш актуальным
        else:
            if not self.settings.cache_incoming():
                return
            sender = await event.get_sender()
            chat_title = None
            if event.is_group:
                chat = await event.get_chat()
                chat_title = getattr(chat, "title", None)
            self.store.save_message(
                event.chat_id, msg.id, getattr(sender, "id", None),
                util.real_name(sender), util.username_of(sender), chat_title,
                new_text, None, util.media_kind(msg), str(msg.date),
            )
```

- [ ] **Step 7: Гейтинг стартового сообщения в `start`**

В методе `start` найти:

```python
        if self.bot_client and self.profile.owner_id:
            try:
                await self.bot_client.send_message(
                    self.profile.owner_id,
                    f"✅ Слежу за аккаунтом <b>{util.html_escape(self.me_name)}</b> "
                    f"— перехваты буду присылать сюда.", parse_mode="html")
```

Заменить первую строку условия на:

```python
        if self.bot_client and self.profile.owner_id and self.settings.enabled:
```

- [ ] **Step 8: Проверить компиляцию**

Run: `python -m py_compile capturer.py`
Expected: без ошибок (пустой вывод, код возврата 0).

- [ ] **Step 9: Коммит**

```bash
git add capturer.py
git commit -m "Capturer: гейтинг захвата по настройкам (не кэшировать отключённое)"
```

---

## Task 5: Команды, реестр и кнопки в run.py

**Files:**
- Modify: `run.py` (импорты, `amain`)

- [ ] **Step 1: Обновить импорты**

В `run.py` заменить блок импортов:

```python
import asyncio
import logging

from telethon import TelegramClient, events

import profiles
from store import Store
from capturer import Capturer
```

на:

```python
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
```

- [ ] **Step 2: Удалить старые WELCOME и `_on_start`**

В `run.py` удалить целиком блок от `WELCOME = (` до конца функции `_on_start` (включая её) — приветствие и обработчики теперь живут в `bot_ui` и внутри `amain`. Должны исчезнуть строки:

```python
WELCOME = (
    "\U0001F575 Привет! Это <b>DIASLOG INTERCEPT</b>.\n\n"
    "Я приношу тебе то, что пытаются скрыть в Telegram:\n"
    "\U0001F5D1 удалённые сообщения\n"
    "✏️ изменённые сообщения\n"
    "\U0001F441 одноразовые фото и видео"
)


async def _on_start(event):
    await event.reply(WELCOME, parse_mode="html")
```

- [ ] **Step 3: Переписать `amain`**

Заменить всю функцию `amain` целиком на:

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
```

- [ ] **Step 4: Проверить компиляцию всего проекта**

Run: `python -m py_compile run.py capturer.py settings.py bot_ui.py profiles.py main.py`
Expected: без ошибок.

- [ ] **Step 5: Прогнать все тесты**

Run: `python -m pytest -v`
Expected: PASS — 10 passed (6 settings + 4 bot_ui).

- [ ] **Step 6: Коммит**

```bash
git add run.py
git commit -m "run.py: кнопка «Меню», /start с настройками, обработчик кнопок"
```

---

## Task 6: Деплой и ручная сквозная проверка

**Files:** нет (проверка в Telegram).

- [ ] **Step 1: Запушить**

```bash
git push
```

- [ ] **Step 2: Пересобрать на dockhost**

В панели dockhost нажать **Redeploy/Пересобрать**, дождаться старта. В логах ожидать:
```
Общий бот-доставщик поднят.
Запущено аккаунтов: 2. ...
```

- [ ] **Step 3: Проверить кнопку «Меню» и приветствие**

В чате с ботом: у поля ввода появилась синяя кнопка **«Меню»** → нажать → `/start` → пришло приветствие с кнопкой **⚙️ Настройки**.

- [ ] **Step 4: Проверить экран настроек**

Нажать ⚙️ Настройки → то же сообщение сменилось на экран с 🟢 и кнопками (Удалённые ✅ / Изменённые ✅ / Одноразовые ✅ / Назад). «Назад» возвращает приветствие.

- [ ] **Step 5: Проверить фильтр «только удалённые»**

В настройках выключить «Изменённые» (станет ❌) и «Одноразовые» (станет ❌). Затем:
- попросить написать в личку и **удалить** сообщение → перехват **приходит**;
- попросить **отредактировать** сообщение → перехват **не приходит**.

- [ ] **Step 6: Проверить общий выключатель**

Нажать «🔴 Выключить бота» (шапка → 🔴). Удалить тестовое сообщение → **ничего не приходит**. Включить обратно → удаление снова приходит.

- [ ] **Step 7: Проверить изоляцию владельцев**

Друг открывает свои настройки и выключает бота у себя → у тебя настройки и доставка не меняются (перехваты по твоему аккаунту продолжают приходить тебе).

---

## Самопроверка плана

- **Покрытие спеки:** каскад «не кэшировать отключённое» → Task 1 (предикаты) + Task 4 (подключение); кнопка «Меню» → Task 5 Step 3; `/start` + ⚙️ → Task 5; экран с переключателями → Task 3 + Task 5; настройки на владельца → `registry` в Task 5; персистентность на `/data` → Task 1 (save/load) + Task 2 (путь); краевой случай не-владельца → Task 5 `on_callback`; дефолт «всё включено» → Task 1. Пробелов нет.
- **Плейсхолдеры:** отсутствуют — в каждом шаге полный код/команда.
- **Согласованность типов:** `Settings.toggle/load/save`, предикаты `wants_deleted/wants_edited/wants_view_once/cache_incoming`, `Profile.settings_path`, `bot_ui.WELCOME/welcome_buttons/settings_text/settings_buttons/parse_toggle/CB_OPEN/CB_BACK/CB_TOGGLE`, `Capturer(..., settings=...)` — имена совпадают во всех задачах.
