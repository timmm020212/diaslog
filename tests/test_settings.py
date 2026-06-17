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
