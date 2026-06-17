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
