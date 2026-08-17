import pytest
from unittest.mock import Mock


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
    return load_module("sensor_monitor.py", monkeypatch)


def test_constants(app):
    assert app.MAX_TEMP == 50.0
    assert app.LIGHT_DELTA == 0.40
    assert app.CONSECUTIVE_LIMIT == 3
    assert app.SENSOR_INTERVAL == 3.0
    assert app.LDR_CHANNEL == 0


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


def test_read_temperature_updates_value(app):
    app.temp_humid_sensor.read_temp_humidity = Mock(
        return_value=(51.0, 40.0)
    )

    assert app.read_temperature() == 51.0
    assert app.current_temp == 51.0


def test_read_light_updates_value(app):
    app.adc.get_adc_value = Mock(return_value=1500)

    assert app.read_light() == 1500
    assert app.current_light == 1500


def test_light_triggered_above_40_percent(app):
    app.baseline_light = 1000
    app.current_light = 1401

    assert app.light_triggered() is True


def test_light_not_triggered_at_40_percent(app):
    app.baseline_light = 1000
    app.current_light = 1400

    assert app.light_triggered() is False


def test_temperature_three_consecutive_readings_trigger(app):
    app.alert_active = False
    app.temp_count = 0
    app.light_count = 0
    app.baseline_light = 1000
    app.lcd_display = Mock()
    app.write_log = Mock()

    def fake_temperature():
        app.current_temp = 51.0
        return 51.0

    def fake_light():
        app.current_light = 0
        return 0

    app.read_temperature = fake_temperature
    app.read_light = fake_light

    for _ in range(3):
        app.monitor_sensors()

    assert app.temp_count == 3
    assert app.alert_active is True
    assert app.alert_reason == ["Temperature"]


def test_one_bad_temperature_does_not_trigger(app):
    app.alert_active = False
    app.temp_count = 0
    app.light_count = 0
    app.baseline_light = 1000
    app.lcd_display = Mock()
    app.write_log = Mock()

    def fake_temperature():
        app.current_temp = 51.0
        return 51.0

    def fake_light():
        app.current_light = 0
        return 0

    app.read_temperature = fake_temperature
    app.read_light = fake_light

    app.monitor_sensors()

    assert app.temp_count == 1
    assert app.alert_active is False


def test_light_three_consecutive_readings_trigger(app):
    app.alert_active = False
    app.temp_count = 0
    app.light_count = 0
    app.baseline_light = 1000
    app.lcd_display = Mock()
    app.write_log = Mock()

    def fake_temperature():
        app.current_temp = 25.0
        return 25.0

    def fake_light():
        app.current_light = 1500
        return 1500

    app.read_temperature = fake_temperature
    app.read_light = fake_light

    for _ in range(3):
        app.monitor_sensors()

    assert app.light_count == 3
    assert app.alert_active is True
    assert app.alert_reason == ["Light"]


def test_temperature_and_light_trigger_together(app):
    app.alert_active = False
    app.temp_count = 0
    app.light_count = 0
    app.baseline_light = 1000
    app.lcd_display = Mock()
    app.write_log = Mock()

    def fake_temperature():
        app.current_temp = 51.0
        return 51.0

    def fake_light():
        app.current_light = 1500
        return 1500

    app.read_temperature = fake_temperature
    app.read_light = fake_light

    for _ in range(3):
        app.monitor_sensors()

    assert app.alert_active is True
    assert app.alert_reason == ["Temperature", "Light"]


def test_alert_clears_after_three_normal_readings(app):
    app.alert_active = True
    app.alert_reason = ["Temperature"]
    app.temp_count = 0
    app.light_count = 0
    app.clear_count = 0
    app.baseline_light = 1000
    app.lcd_display = Mock()
    app.write_log = Mock()

    def fake_temperature():
        app.current_temp = 25.0
        return 25.0

    def fake_light():
        app.current_light = 500
        return 500

    app.read_temperature = fake_temperature
    app.read_light = fake_light

    for _ in range(3):
        app.monitor_sensors()

    assert app.alert_active is False
    assert app.alert_reason == []
