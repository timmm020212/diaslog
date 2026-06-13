"""Профиль = один аккаунт. Читает свой .env и задаёт пути к данным.

  .env          -> профиль "default"  -> папка data/
  .env.friend   -> профиль "friend"   -> папка data-friend/
"""
import os

from dotenv import dotenv_values

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# В облаке задаём DIASLOG_DATA_DIR=/data — тогда и конфиги (.env), и данные профилей
# (сессии/базы/медиа) берутся из одного примонтированного диска. Локально — как было.
DATA_ROOT = os.getenv("DIASLOG_DATA_DIR", "").strip()
CONFIG_DIR = DATA_ROOT or BASE_DIR


def _bool(v, default=True):
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _int(v, default=0):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


class Profile:
    def __init__(self, name, env_file):
        self.name = name
        vals = dotenv_values(env_file)
        self.api_id = _int(vals.get("API_ID"))
        self.api_hash = (vals.get("API_HASH") or "").strip()
        self.bot_token = (vals.get("BOT_TOKEN") or "").strip()
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
        self.bot_session = os.path.join(self.data_dir, "bot_session")
        os.makedirs(self.media_dir, exist_ok=True)

    @property
    def configured(self):
        return bool(self.api_id and self.api_hash and self.bot_token)

    @property
    def session_exists(self):
        return os.path.exists(self.user_session + ".session")

    @property
    def label(self):
        return "Мой аккаунт" if self.name == "default" else self.name


def discover():
    """Находит все профили: .env и .env.<name> (кроме *.example)."""
    found = {}
    default_env = os.path.join(CONFIG_DIR, ".env")
    if os.path.exists(default_env):
        found["default"] = Profile("default", default_env)
    for fname in sorted(os.listdir(CONFIG_DIR)):
        if fname.startswith(".env.") and not fname.endswith(".example"):
            name = fname[len(".env."):]
            found[name] = Profile(name, os.path.join(CONFIG_DIR, fname))
    return found
