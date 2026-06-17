# Админ-панель: добавление/удаление отслеживаемых аккаунтов

Дата: 2026-06-17

## Цель

Дать админу (только `ttmmde`, id из `ADMIN_ID`) управлять списком отслеживаемых
аккаунтов прямо из чата с ботом: добавлять новый аккаунт через визард входа
(телефон → код → 2FA) и удалять существующий. Перехваты добавленного аккаунта идут
его же владельцу (`owner_id = id залогиненного аккаунта`). Добавленные аккаунты
переживают перезапуск контейнера.

Подход — «полный визард в боте» (вариант A из брейншторминга).

## Ограничения и риски (приняты осознанно)

- **Telegram сжигает код**, отправленный текстом внутри Telegram. Поэтому код
  вводится «разбито» (`1 2 3 4 5`), бот выпаривает цифры (`isdigit`).
- **Вход из дата-центра** может вызвать повторный запрос кода/ограничение — тот же
  риск, что при входе через консоль.
- **Код и 2FA-пароль видны** в чате админа с ботом. Для своих аккаунтов приемлемо.
- Один админ, один визард за раз (без параллельных входов).

## Идентификация админа

- `BotConfig` читает `ADMIN_ID` из `.env.bot` (новое поле `admin_id`).
- Кнопка «🛠 Админ-панель» в приветствии показывается только если
  `event.sender_id == bot_cfg.admin_id`.
- Все админ-callback'и и сообщения визарда обрабатываются только для админа; иначе
  «нет доступа».

## Интерфейс

### Приветствие
`welcome_buttons(is_admin)` — добавляет ряд `[🛠 Админ-панель]`, если `is_admin`.

### Экран админ-панели
`admin_text(accounts)` + `admin_buttons()`:
```
🛠 Админ-панель
Аккаунтов под наблюдением: N
• <label1>
• <label2>

[ ➕ Добавить аккаунт ]
[ ➖ Удалить аккаунт ]
[ ◀️ Назад ]
```

### Удаление
`remove_list_buttons(accounts)` — по кнопке на аккаунт (`admin:rm:<name>`) + «Назад».
Выбор → `confirm_remove_buttons(name)`: `[✅ Да, удалить]` (`admin:rmok:<name>`) /
`[◀️ Отмена]`. Подтверждение → стоп слежки и удаление файлов аккаунта.

### Визард добавления (диалог сообщениями)
Каждый шаг — сообщение бота с инлайн-кнопкой `[Отмена]` (`admin:cancel`):
1. ➕ → «Пришли номер телефона (с `+`)». Состояние `phone`.
2. Админ шлёт номер → `client.send_code_request(phone)` → «Код пришёл в Telegram.
   Введи его **разбито** (`1 2 3 4 5`)». Состояние `code`.
3. Админ шлёт код → цифры выпариваются → `client.sign_in(phone, code, phone_code_hash)`.
   - Успех → финализация.
   - `SessionPasswordNeededError` → «Пришли пароль облака (2FA)». Состояние `password`.
   - `PhoneCodeInvalidError` / `PhoneCodeExpiredError` → сообщение об ошибке, остаёмся
     на шаге `code` (просим заново) / предлагаем начать сначала.
4. Админ шлёт пароль → `client.sign_in(password=...)` → финализация.

Ошибки `FloodWaitError`, `PhoneNumberInvalidError` → понятное сообщение, визард сброшен.

## Финализация входа

1. `me = await client.get_me()`; `await client.disconnect()`.
2. `name = f"id{me.id}"`; `owner_id = me.id`.
3. Создать `Profile(name, env_path)` — он создаёт `data_dir` (через `makedirs media_dir`).
4. Перенести файл сессии: `os.replace(<temp>.session, <data_dir>/user_session.session)`.
5. Записать `.env.<name>` в `CONFIG_DIR` (`/data`): `API_ID`/`API_HASH` (общие из
   `.env.bot`), `OWNER_ID=me.id`, `CACHE_MEDIA=true`, `RETENTION_DAYS=7`.
6. `await manager.start_profile(profile)` — поднять `Capturer`, добавить в реестры.
7. Ответ: «✅ Аккаунт <label> добавлен. Перехваты пойдут владельцу — пусть нажмёт
   `/start` боту.»

Временная сессия входа: `TelegramClient(os.path.join(CONFIG_DIR, f".login_{admin_id}"), ...)`
→ файл `.login_<admin_id>.session`, после успеха переносится в папку аккаунта.

## Архитектура

### `admin.py` (новый) — `AccountManager`
Владеет жизненным циклом аккаунтов и визардом, чтобы `run.py` не разбухал.
```
class AccountManager:
    def __init__(self, bot, bot_cfg, store_factory)
    accounts: dict[str, Capturer]            # name -> запущенный Capturer
    registry: dict[int, tuple[str, Settings]] # owner_id -> (label, Settings)
    wizards:  dict[int, Wizard]               # admin_id -> состояние входа

    async def start_profile(self, profile)    # Settings.load + Capturer + start + register
    async def remove(self, name)              # stop + удалить .env.<name> и data_dir
    def list_labels(self)                     # для admin_text/remove-кнопок

    # визард
    async def begin_add(self, admin_id) -> prompt
    async def feed_message(self, admin_id, text) -> reply   # шаги phone/code/password
    async def cancel(self, admin_id)
```
`Wizard` — маленький держатель: `client`, `phone`, `phone_code_hash`, `step`.

### `profiles.py`
- `BotConfig.admin_id = _int(vals.get("ADMIN_ID"))`.
- Хелперы: `env_path_for(name)` → `CONFIG_DIR/.env` для `default`, иначе
  `CONFIG_DIR/.env.<name>` (учитывает, что у `default` конфиг — это `.env`);
  `write_profile_env(name, api_id, api_hash, owner_id)` → запись файла по `env_path_for`;
  `delete_profile(profile)` → удалить `env_path_for(profile.name)` и рекурсивно `data_dir`.

### `bot_ui.py`
- `welcome_buttons(is_admin=False)`; `admin_text(labels)`; `admin_buttons()`;
  `remove_list_buttons(items)`; `confirm_remove_buttons(name)`; `wizard_cancel_buttons()`.
- Константы/парсеры callback: `admin:open`, `admin:add`, `admin:remove`, `admin:cancel`,
  префиксы `admin:rm:`, `admin:rmok:`; `parse_admin(data)` → ('rm', name) и т.п.

### `run.py`
- Создаёт `AccountManager(bot, bot_cfg, Store)`; стартует найденные профили через него.
- Реестр настроек берётся из `manager.registry` (настройки-панель из прошлой фичи).
- Регистрирует общий `events.NewMessage()` обработчик: если у `sender_id` активен визард
  и текст не команда (`/`) — `manager.feed_message`.
- В `on_callback`: ветки `admin:*` (только для `admin_id`) — открыть панель, добавить,
  список/подтверждение удаления, отмена.
- `on_start`/`welcome` передаёт `is_admin = (sender_id == bot_cfg.admin_id)`.

## Хранение и перезапуск

Добавленные аккаунты лежат как `.env.<id>` в `/data` + сессия в `/data/<id>/`.
Существующий `profiles.discover()` подхватывает их при старте — после перезапуска
контейнера слежка восстанавливается без участия админа. Удаление стирает и env-файл,
и `data_dir`.

## Тестирование

- Юнит-тесты (чистая логика): парсинг callback `parse_admin`, выпаривание цифр кода,
  `admin_text`/кнопки содержат нужные элементы, `write_profile_env` → `Profile`
  читает обратно корректные значения, `delete_profile` удаляет файлы (на `tmp_path`).
- Ручная проверка (Telethon/диалог): полный визард добавления (телефон→код→2FA),
  доставка владельцу, удаление, переживание перезапуска.

## Что НЕ входит (YAGNI)

- Несколько админов / роли.
- Редактирование чужих фильтров из админки.
- Просмотр истории перехватов.
- Параллельные визарды входа.
