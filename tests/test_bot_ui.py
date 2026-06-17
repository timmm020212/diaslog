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


def test_welcome_buttons_admin_flag():
    admin = [btn.text for row in bot_ui.welcome_buttons(is_admin=True) for btn in row]
    plain = [btn.text for row in bot_ui.welcome_buttons(is_admin=False) for btn in row]
    assert any("Админ" in t for t in admin)
    assert not any("Админ" in t for t in plain)


def test_parse_admin_actions():
    assert bot_ui.parse_admin(b"admin:open") == ("open", None)
    assert bot_ui.parse_admin(b"admin:add") == ("add", None)
    assert bot_ui.parse_admin(b"admin:remove") == ("remove", None)
    assert bot_ui.parse_admin(b"admin:cancel") is None  # handled publicly before parse_admin
    assert bot_ui.parse_admin(b"admin:rm:id5") == ("rm", "id5")
    assert bot_ui.parse_admin(b"admin:rmok:id5") == ("rmok", "id5")
    assert bot_ui.parse_admin(b"toggle:deleted") is None


def test_admin_text_lists_labels():
    text = bot_ui.admin_text(["timur", "Илья"])
    assert "Админ-панель" in text
    assert "timur" in text and "Илья" in text


def test_welcome_has_connect_button():
    labels = [b.text for row in bot_ui.welcome_buttons() for b in row]
    assert any("Подключиться" in t for t in labels)


def test_connect_method_buttons_have_code_and_qr():
    labels = [b.text for row in bot_ui.connect_method_buttons() for b in row]
    assert any("код" in t.lower() for t in labels)
    assert any("qr" in t.lower() for t in labels)
