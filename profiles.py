"""Профиль = один аккаунт, за которым следим. Доставку делает ОДИН общий бот.

  .env          -> аккаунт "default" -> папка data/
  .env.friend   -> аккаунт "friend"  -> папка data-friend/
  .env.bot      -> общий бот-доставщик (один токен на всех)

Перехваты каждого аккаунта уходят его владельцу (OWNER_ID) через общий бот, поэтому
у разных людей в одном боте — только свои уведомления.
"""
import os

from dotenv import dotenv_values

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# В облаке задаём DIASLOG_DATA_DIR=/data — тогда и конфиги (.env*), и данные
# (сессии/базы/медиа) берутся из одного примонтированного диска. Локально — как было.
DATA_ROOT = os.getenv("DIASLOG_DATA_DIR", "").strip()
CONFIG_DIR = DATA_ROOT or BASE_DIR

BOT_ENV = ".env.bot"  # файл с конфигом общего бота-доставщика


def _bool(v, default=True):
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _int(v, default=0):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


class BotConfig:
    """Общий бот-доставщик: один на всех. Берёт перехваты у каждого аккаунта и
    шлёт их владельцу этого аккаунта (Profile.owner_id). Токен — от @BotFather,
    api_id/api_hash — любые валидные (можно те же, что у аккаунта)."""

    def __init__(self, env_file):
        vals = dotenv_values(env_file)
        self.token = (vals.get("BOT_TOKEN") or "").strip()
        self.api_id = _int(vals.get("API_ID"))
        self.api_hash = (vals.get("API_HASH") or "").strip()
        self.admin_id = _int(vals.get("ADMIN_ID"))
        # Токен/порт relay можно задать ИЛИ в .env.bot, ИЛИ переменной окружения
        # контейнера (в панели DockHost — так проще, без правки файла на диске).
        self.relay_token = (vals.get("AUTH_RELAY_TOKEN")
                            or os.getenv("AUTH_RELAY_TOKEN") or "").strip()
        self.relay_port = _int(vals.get("AUTH_RELAY_PORT")
                               or os.getenv("AUTH_RELAY_PORT"), 8080)
        bot_dir = os.path.join(DATA_ROOT or BASE_DIR, "bot")
        os.makedirs(bot_dir, exist_ok=True)
        self.session = os.path.join(bot_dir, "bot_session")

    @property
    def configured(self):
        return bool(self.token and self.api_id and self.api_hash)


class Profile:
    """Один аккаунт, за которым следим (юзербот). Доставку делает общий бот."""

    def __init__(self, name, env_file):
        self.name = name
        vals = dotenv_values(env_file)
        self.api_id = _int(vals.get("API_ID"))
        self.api_hash = (vals.get("API_HASH") or "").strip()
        self.owner_id = _int(vals.get("OWNER_ID"))
        self.cache_media = _bool(vals.get("CACHE_MEDIA"), True)
        self.retention_days = _int(vals.get("RETENTION_DAYS"), 7)

        if DATA_ROOT:
            self.data_dir = os.path.join(DATA_ROOT, name)
        else:
            sub = "data" if name == "default" else f"data-{name}"
            self.data_dir = os.path.join(BASE_DIR, sub)
        self.media_dir = os.path.join(self.data_dir, "media")
        self.db_path = os.path.join(self.data_dir, "cache.db")
        self.user_session = os.path.join(self.data_dir, "user_session")
        self.settings_path = os.path.join(self.data_dir, "settings.json")
        os.makedirs(self.media_dir, exist_ok=True)

    @property
    def configured(self):
        return bool(self.api_id and self.api_hash)

    @property
    def session_exists(self):
        return os.path.exists(self.user_session + ".session")

    @property
    def label(self):
        return "Мой аккаунт" if self.name == "default" else self.name


def load_bot():
    """Конфиг общего бота из .env.bot (или None, если файла нет)."""
    path = os.path.join(CONFIG_DIR, BOT_ENV)
    if not os.path.exists(path):
        return None
    return BotConfig(path)


def discover():
    """Находит все аккаунты: .env и .env.<name> (кроме *.example и .env.bot)."""
    os.makedirs(CONFIG_DIR, exist_ok=True)  # в облаке /data может быть пустым/новым
    found = {}
    default_env = os.path.join(CONFIG_DIR, ".env")
    if os.path.exists(default_env):
        found["default"] = Profile("default", default_env)
    for fname in sorted(os.listdir(CONFIG_DIR)):
        if (fname.startswith(".env.") and not fname.endswith(".example")
                and fname != BOT_ENV):
            name = fname[len(".env."):]
            found[name] = Profile(name, os.path.join(CONFIG_DIR, fname))
    return found


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
