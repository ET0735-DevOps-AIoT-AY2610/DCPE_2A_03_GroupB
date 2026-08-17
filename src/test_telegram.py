import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest


BASE = Path(__file__).resolve().parent


@pytest.fixture
def app(monkeypatch, tmp_path):
    """Import telegram.py with a temporary working directory."""
    monkeypatch.chdir(tmp_path)

    spec = importlib.util.spec_from_file_location(
        "telegram_test_app",
        BASE / "telegram.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["telegram_test_app"] = module
    spec.loader.exec_module(module)

    return module


def test_constants(app):
    assert app.NOTIFICATION_RETRY == 10.0
    assert app.LOG_FILE == Path("communication_events.csv")


def test_env_file_is_loaded(app, tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "FIRE_UNIT_ID=UNIT-TEST\n"
        "TELEGRAM_BOT_TOKEN=test-token\n"
        "TELEGRAM_CHAT_ID=12345\n"
    )

    app.load_env_file()

    assert app.os.environ["FIRE_UNIT_ID"] == "UNIT-TEST"
    assert app.os.environ["TELEGRAM_BOT_TOKEN"] == "test-token"
    assert app.os.environ["TELEGRAM_CHAT_ID"] == "12345"


def test_telegram_message_returns_false_when_not_configured(app):
    app.TELEGRAM_BOT_TOKEN = ""
    app.TELEGRAM_CHAT_ID = ""

    assert app.send_telegram_message("test message") is False


def test_telegram_message_success(app, monkeypatch):
    app.TELEGRAM_BOT_TOKEN = "fake-token"
    app.TELEGRAM_CHAT_ID = "12345"

    response = Mock()
    response.status = 200

    fake_urlopen = Mock(return_value=response)
    monkeypatch.setattr(app.request, "urlopen", fake_urlopen)

    assert app.send_telegram_message("hello") is True
    fake_urlopen.assert_called_once()


def test_telegram_message_failure_returns_false(app, monkeypatch):
    app.TELEGRAM_BOT_TOKEN = "fake-token"
    app.TELEGRAM_CHAT_ID = "12345"

    monkeypatch.setattr(
        app.request,
        "urlopen",
        Mock(side_effect=OSError("network failure"))
    )

    assert app.send_telegram_message("hello") is False


def test_telegram_photo_returns_false_when_not_configured(app, tmp_path):
    app.TELEGRAM_BOT_TOKEN = ""
    app.TELEGRAM_CHAT_ID = ""

    photo = tmp_path / "fire.jpg"
    photo.write_bytes(b"fake-jpeg")

    assert app.send_telegram_photo(photo) is False


def test_telegram_photo_returns_false_when_photo_missing(app):
    app.TELEGRAM_BOT_TOKEN = "fake-token"
    app.TELEGRAM_CHAT_ID = "12345"

    assert app.send_telegram_photo("missing.jpg") is False


def test_telegram_photo_success(app, tmp_path, monkeypatch):
    app.TELEGRAM_BOT_TOKEN = "fake-token"
    app.TELEGRAM_CHAT_ID = "12345"
    app.UNIT_ID = "UNIT-TEST"

    photo = tmp_path / "fire.jpg"
    photo.write_bytes(b"fake-jpeg")

    response = Mock()
    response.status = 200

    fake_urlopen = Mock(return_value=response)
    monkeypatch.setattr(app.request, "urlopen", fake_urlopen)

    assert app.send_telegram_photo(photo) is True
    fake_urlopen.assert_called_once()


def test_telegram_photo_network_failure_returns_false(app, tmp_path, monkeypatch):
    app.TELEGRAM_BOT_TOKEN = "fake-token"
    app.TELEGRAM_CHAT_ID = "12345"

    photo = tmp_path / "fire.jpg"
    photo.write_bytes(b"fake-jpeg")

    monkeypatch.setattr(
        app.request,
        "urlopen",
        Mock(side_effect=OSError("network failure"))
    )

    assert app.send_telegram_photo(photo) is False


def test_alert_message_contains_required_fields(app):
    app.UNIT_ID = "UNIT-TEST"
    app.alert_source = "Sensor Alert"
    app.alert_reason = ["Temperature", "Light"]
    app.current_temp = 51.0
    app.current_light = 1500

    message = app.build_alert_message()

    assert "SMART FIRE ALERT" in message
    assert "Unit: UNIT-TEST" in message
    assert "Sensor Alert" in message
    assert "Temperature, Light" in message
    assert "51.0 C" in message
    assert "Light: 1500" in message


def test_alert_notification_worker_logs_success(app):
    app.send_telegram_message = Mock(return_value=True)
    app.write_log = Mock()

    app.alert_notification_worker("test alert")

    app.send_telegram_message.assert_called_once_with("test alert")
    app.write_log.assert_called_once_with(
        "NOTIFICATION_SENT",
        "Telegram text sent"
    )


def test_alert_notification_worker_does_not_log_failure(app):
    app.send_telegram_message = Mock(return_value=False)
    app.write_log = Mock()

    app.alert_notification_worker("test alert")

    app.send_telegram_message.assert_called_once_with("test alert")
    app.write_log.assert_not_called()


def test_one_shot_worker_logs_when_requested(app):
    app.send_telegram_message = Mock(return_value=True)
    app.write_log = Mock()

    app.one_shot_telegram_worker("test alert", "OVERRIDE")

    app.write_log.assert_called_once_with(
        "OVERRIDE",
        "Telegram sent"
    )


def test_one_shot_worker_does_not_log_without_event(app):
    app.send_telegram_message = Mock(return_value=True)
    app.write_log = Mock()

    app.one_shot_telegram_worker("test alert")

    app.write_log.assert_not_called()


def test_photo_queue_exists(app):
    assert app.photo_upload_queue is not None
    assert hasattr(app.photo_upload_queue, "put")
    assert hasattr(app.photo_upload_queue, "get")


def test_event_log_is_trimmed_to_100_records(app):
    app.LOG_FILE = Path("communication_events.csv")

    for i in range(105):
        app.write_log("TEST", str(i))

    with app.LOG_FILE.open("r", newline="") as file:
        rows = file.readlines()

    assert len(rows) == 101
