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
    assert bot_ui.parse_toggle(b"toggle:groups") == "groups"


def test_settings_buttons_include_groups():
    labels = [btn.text for row in bot_ui.settings_buttons(_settings()) for btn in row]
    assert any("Группы" in label for label in labels)


def test_parse_toggle_returns_none_for_others():
    assert bot_ui.parse_toggle(b"open_settings") is None
    assert bot_ui.parse_toggle(b"back") is None
