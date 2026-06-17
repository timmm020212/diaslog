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
        if self.path is None:
            return  # in-memory дефолт (терминальный режим) — некуда сохранять
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
