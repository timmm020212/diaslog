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
from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.types import BotCommand, BotCommandScopeDefault
from telethon.errors import MessageNotModifiedError, MessageIdInvalidError

import profiles
import bot_ui
from store import Store
from admin import AccountManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("diaslog.run")


async def _safe_answer(event, *args, **kwargs):
    """Гасит «часики» на кнопке. Токен мог протухнуть (медленная операция или
    переподключение бота) — тогда answer() кидает QueryIdInvalidError; глотаем."""
    try:
        await event.answer(*args, **kwargs)
    except Exception:
        pass


async def amain():
    found = profiles.discover()
    bot_cfg = profiles.load_bot()

    if not bot_cfg or not bot_cfg.configured:
        log.warning("Нет общего бота: создай %s/.env.bot с BOT_TOKEN / API_ID / API_HASH "
                    "и перезапусти.", profiles.CONFIG_DIR)
        await asyncio.Event().wait()
        return

    if not found and not bot_cfg.admin_id:
        log.warning("Аккаунтов нет (нет .env в %s) и ADMIN_ID не задан. Создай .env / "
                    ".env.friend (или задай ADMIN_ID, чтобы добавлять аккаунты из "
                    "админ-панели) и перезапусти.", profiles.CONFIG_DIR)
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
        await _safe_answer(event)  # гасим часики сразу, пока токен свеж (до медленного connect)
        try:
            if kind == "open":
                await event.edit(bot_ui.admin_text(manager.labels()), parse_mode="html",
                                 buttons=bot_ui.admin_buttons())
            elif kind == "add":
                await manager.begin_add(event.sender_id)
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
        except (MessageNotModifiedError, MessageIdInvalidError):
            pass  # сообщение не изменилось/устарело — игнорируем

    async def on_start(event):
        await event.respond(bot_ui.WELCOME, parse_mode="html",
                            buttons=bot_ui.welcome_buttons(is_admin(event)))

    async def on_callback(event):
        data = event.data
        if data == bot_ui.CB_BACK:
            await event.edit(bot_ui.WELCOME, parse_mode="html",
                             buttons=bot_ui.welcome_buttons(is_admin(event)))
            await _safe_answer(event)
            return
        action = bot_ui.parse_admin(data)
        if action is not None:
            if not is_admin(event):
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

    async def on_message(event):
        if not is_admin(event):
            return
        if event.raw_text.startswith("/"):
            return
        if event.sender_id not in manager.wizards:
            return
        reply = await manager.feed_message(event.sender_id, event.raw_text)
        if reply:
            still = event.sender_id in manager.wizards
            buttons = (bot_ui.wizard_cancel_buttons() if still
                       else bot_ui.admin_buttons())
            await event.respond(reply, parse_mode="html", buttons=buttons)

    bot.add_event_handler(on_start, events.NewMessage(pattern="/start"))
    bot.add_event_handler(on_callback, events.CallbackQuery())
    bot.add_event_handler(on_message, events.NewMessage())

    if manager.accounts:
        log.info("Запущено аккаунтов: %d. Слежу за чатами, доставляю в Telegram.",
                 len(manager.accounts))
    else:
        log.warning("Ни один аккаунт не запущен (можно добавить через админ-панель).")
    await asyncio.Event().wait()  # держим цикл живым — на нём работают обработчики Telethon


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        log.info("Остановлено пользователем.")
