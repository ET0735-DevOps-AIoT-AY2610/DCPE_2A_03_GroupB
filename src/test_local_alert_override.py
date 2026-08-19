from unittest.mock import Mock
import pytest


import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import Mock


BASE = Path(__file__).resolve().parent


def fake_module(name):
    module = types.ModuleType(name)
    module.init = Mock()
    return module


def load_module(filename, monkeypatch):
    hal = types.ModuleType("hal")
    hal.__path__ = []

    modules = {
        "hal.hal_lcd": fake_module("hal.hal_lcd"),
        "hal.hal_adc": fake_module("hal.hal_adc"),
        "hal.hal_temp_humidity_sensor": fake_module(
            "hal.hal_temp_humidity_sensor"
        ),
        "hal.hal_led": fake_module("hal.hal_led"),
        "hal.hal_buzzer": fake_module("hal.hal_buzzer"),
        "hal.hal_keypad": fake_module("hal.hal_keypad"),
        "hal.hal_input_switch": fake_module("hal.hal_input_switch"),
        "hal.hal_servo": fake_module("hal.hal_servo"),
        "hal.hal_rfid_reader": fake_module("hal.hal_rfid_reader"),
    }

    modules["hal.hal_adc"].get_adc_value = Mock(return_value=500)
    modules["hal.hal_temp_humidity_sensor"].read_temp_humidity = Mock(
        return_value=(25.0, 50.0)
    )
    modules["hal.hal_input_switch"].read_slide_switch = Mock(
        return_value=False
    )
    modules["hal.hal_servo"].set_servo_position = Mock()
    modules["hal.hal_buzzer"].turn_on = Mock()
    modules["hal.hal_buzzer"].turn_off = Mock()
    modules["hal.hal_led"].set_output = Mock()
    modules["hal.hal_keypad"].get_key = Mock()

    class FakeLCD:
        def lcd_clear(self):
            pass

        def lcd_display_string(self, text, line):
            pass

    modules["hal.hal_lcd"].lcd = FakeLCD

    class FakeRFID:
        def read_id_no_block(self):
            return None

    modules["hal.hal_rfid_reader"].SimpleMFRC522 = FakeRFID

    for name, module in modules.items():
        setattr(hal, name.split(".")[-1], module)
        monkeypatch.setitem(sys.modules, name, module)

    monkeypatch.setitem(sys.modules, "hal", hal)

    picamera2 = types.ModuleType("picamera2")

    class FakePicamera2:
        def create_still_configuration(self, **kwargs):
            return kwargs

        def configure(self, config):
            self.config = config

        def start(self):
            pass

        def capture_file(self, filename):
            Path(filename).write_bytes(b"fake-jpeg")

    picamera2.Picamera2 = FakePicamera2
    monkeypatch.setitem(sys.modules, "picamera2", picamera2)

    monkeypatch.chdir(BASE)

    path = BASE / filename
    spec = importlib.util.spec_from_file_location(
        filename.replace(".py", "").replace("-", "_"),
        path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def app(monkeypatch):
    return load_module("local_alert_override.py", monkeypatch)


def test_constants(app):
    assert app.SPRINKLER_ON_ANGLE == 180
    assert app.SPRINKLER_OFF_ANGLE == 0
    assert app.OVERRIDE_DELAY == 10.0
    assert app.RFID_REPEAT_DELAY == 2.0


def test_lcd_limits_each_line_to_16_characters(app):
    app.lcd = Mock()

    app.lcd_display(
        "12345678901234567890",
        "ABCDEFGHIJKLMNOPQRST"
    )

    assert app.lcd.lcd_display_string.call_args_list[0].args == (
        "1234567890123456", 1
    )
    assert app.lcd.lcd_display_string.call_args_list[1].args == (
        "ABCDEFGHIJKLMNOP", 2
    )


def test_sprinkler_on_uses_180_degrees(app):
    app.lcd_display = Mock()
    app.write_log = Mock()

    app.sprinkler_on()

    app.servo.set_servo_position.assert_called_once_with(180)


def test_sprinkler_off_uses_zero_degrees(app):
    app.write_log = Mock()

    app.sprinkler_off()

    app.servo.set_servo_position.assert_called_once_with(0)


def test_manual_alert_sets_expected_state(app):
    app.lcd_display = Mock()
    app.write_log = Mock()
    app.sprinkler_on = Mock()

    app.start_alert("Manual Alert", ["Manual Button"])

    assert app.alert_active is True
    assert app.alert_source == "Manual Alert"
    assert app.alert_reason == ["Manual Button"]
    app.sprinkler_on.assert_called_once()


def test_keypad_correct_password_accepts_override(app, monkeypatch):
    app.OVERRIDE_PASSWORD = "1234"
    app.alert_active = True
    app.lcd_display = Mock()
    app.stop_local_alert = Mock()
    app.sprinkler_off = Mock()
    app.write_log = Mock()

    monkeypatch.setattr(app.time, "monotonic", lambda: 100.0)

    app.handle_override_success("Keypad")

    assert app.override_silence_until == 110.0
    app.stop_local_alert.assert_called_once()
    app.sprinkler_off.assert_called_once()
    app.lcd_display.assert_called_once_with("Override", "Accepted")


def test_keypad_digits_build_password(app):
    app.alert_active = True
    app.password_buffer = ""
    app.lcd_display = Mock()

    for key in "1234":
        app.shared_keypad_queue.put(key)

    app.process_keypad()

    assert app.password_buffer == "1234"


def test_keypad_wrong_password_is_rejected(app):
    app.OVERRIDE_PASSWORD = "1234"
    app.alert_active = True
    app.password_buffer = "9999"
    app.lcd_display = Mock()
    app.write_log = Mock()

    app.shared_keypad_queue.put("#")
    app.process_keypad()

    assert app.password_buffer == ""
    app.lcd_display.assert_any_call("Wrong Code", "Try Again")


def test_authorised_rfid_accepts_override(app, monkeypatch):
    app.AUTHORIZED_RFID_IDS = {"977573770339"}
    app.alert_active = True
    app.lcd_display = Mock()
    app.stop_local_alert = Mock()
    app.sprinkler_off = Mock()
    app.write_log = Mock()

    class RFID:
        def read_id_no_block(self):
            return 977573770339

    app.rfid_reader = RFID()

    monkeypatch.setattr(app.time, "monotonic", lambda: 100.0)

    app.process_rfid_override()

    assert app.override_silence_until == 110.0
    app.lcd_display.assert_called_once_with("Override", "Accepted")


def test_unauthorised_rfid_is_rejected(app):
    app.AUTHORIZED_RFID_IDS = {"111111111"}
    app.alert_active = True
    app.write_log = Mock()
    app.handle_override_success = Mock()

    class RFID:
        def read_id_no_block(self):
            return 977573770339

    app.rfid_reader = RFID()

    app.process_rfid_override()

    app.handle_override_success.assert_not_called()


def test_override_silence_is_10_seconds(app, monkeypatch):
    app.override_silence_until = 110.0
    app.stop_local_alert_outputs = Mock()
    app.sprinkler_off = Mock()

    monkeypatch.setattr(app.time, "monotonic", lambda: 105.0)

    app.update_local_alert()

    app.stop_local_alert_outputs.assert_called_once()


def test_clear_alert_resets_state(app):
    app.alert_active = True
    app.alert_source = "Manual Alert"
    app.alert_reason = ["Manual Button"]
    app.override_silence_until = 123.0
    app.stop_local_alert_outputs = Mock()
    app.sprinkler_off = Mock()
    app.write_log = Mock()

    app.clear_alert("Manual switch turned OFF")

    assert app.alert_active is False
    assert app.alert_source == ""
    assert app.alert_reason == []
    assert app.override_silence_until == 0.0
    app.stop_local_alert_outputs.assert_called_once()
    app.sprinkler_off.assert_called_once()
