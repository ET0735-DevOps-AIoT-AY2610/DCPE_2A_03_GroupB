"""
Pytest tests for main.py.

Style:
    input/state -> call function -> assert expected result

A small import shim is used only so main.py can be imported on a PC
without Raspberry Pi hardware/libraries.
"""

import csv
import os
import sys
import types
import queue
from pathlib import Path

import pytest


# ================================================================
# IMPORT SETUP
# ================================================================

def _fake_module(name):
    return types.ModuleType(name)


picamera2 = _fake_module("picamera2")


class FakePicamera2:
    def create_still_configuration(self, **kwargs):
        return kwargs

    def configure(self, config):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass

    def capture_file(self, filename):
        Path(filename).write_bytes(b"fake-jpeg")


picamera2.Picamera2 = FakePicamera2

hal = _fake_module("hal")
hal.__path__ = []

for name in [
    "hal_led",
    "hal_lcd",
    "hal_adc",
    "hal_buzzer",
    "hal_keypad",
    "hal_input_switch",
    "hal_servo",
    "hal_temp_humidity_sensor",
    "hal_rfid_reader",
]:
    module = _fake_module("hal." + name)
    setattr(hal, name, module)
    sys.modules["hal." + name] = module

sys.modules["picamera2"] = picamera2
sys.modules["hal"] = hal

import main


# ================================================================
# load_env_file()
# ================================================================


def test_load_env_file_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FIRE_TEST_VALUE", raising=False)

    result = main.load_env_file()

    assert result is None



def test_load_env_file_reads_values(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        '# comment\n'
        'FIRE_TEST_VALUE="hello"\n'
        "FIRE_TEST_NUMBER=123\n"
        "INVALID_LINE\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FIRE_TEST_VALUE", raising=False)
    monkeypatch.delenv("FIRE_TEST_NUMBER", raising=False)

    main.load_env_file()

    assert os.environ["FIRE_TEST_VALUE"] == "hello"
    assert os.environ["FIRE_TEST_NUMBER"] == "123"



def test_timestamp_format():
    result = main.timestamp()

    date_part, time_part = result.split(" ")

    assert len(date_part) == 10
    assert len(time_part) == 8
    assert date_part[4] == "-"
    assert date_part[7] == "-"
    assert time_part[2] == ":"
    assert time_part[5] == ":"



def test_write_log_creates_file(tmp_path, monkeypatch):
    main.LOG_FILE = tmp_path / "fire_events.csv"

    monkeypatch.setattr(
        main,
        "timestamp",
        lambda: "2026-08-16 19:00:00",
    )

    main.write_log("TEST", "hello")

    with main.LOG_FILE.open("r", newline="") as file:
        rows = list(csv.reader(file))

    assert rows[0] == ["timestamp", "event", "details"]
    assert rows[1] == [
        "2026-08-16 19:00:00",
        "TEST",
        "hello",
    ]



def test_write_log_appends_second_event(tmp_path, monkeypatch):
    main.LOG_FILE = tmp_path / "fire_events.csv"

    times = iter([
        "2026-08-16 19:00:00",
        "2026-08-16 19:01:00",
    ])
    monkeypatch.setattr(main, "timestamp", lambda: next(times))

    main.write_log("EVENT1", "one")
    main.write_log("EVENT2", "two")

    with main.LOG_FILE.open("r", newline="") as file:
        rows = list(csv.reader(file))

    assert len(rows) == 3
    assert rows[1][1] == "EVENT1"
    assert rows[2][1] == "EVENT2"



def test_trim_event_log_within_limit(tmp_path):
    main.LOG_FILE = tmp_path / "events.csv"
    main.LOG_LIMIT = 3

    rows = [
        ["timestamp", "event", "details"],
        ["1", "A", "a"],
        ["2", "B", "b"],
    ]

    with main.LOG_FILE.open("w", newline="") as file:
        csv.writer(file).writerows(rows)

    main.trim_event_log()

    with main.LOG_FILE.open("r", newline="") as file:
        result = list(csv.reader(file))

    assert result == rows



def test_trim_event_log_over_limit(tmp_path):
    main.LOG_FILE = tmp_path / "events.csv"
    main.LOG_LIMIT = 3

    rows = [
        ["timestamp", "event", "details"],
        ["1", "A", "a"],
        ["2", "B", "b"],
        ["3", "C", "c"],
        ["4", "D", "d"],
        ["5", "E", "e"],
    ]

    with main.LOG_FILE.open("w", newline="") as file:
        csv.writer(file).writerows(rows)

    main.trim_event_log()

    with main.LOG_FILE.open("r", newline="") as file:
        result = list(csv.reader(file))

    assert result == [
        ["timestamp", "event", "details"],
        ["3", "C", "c"],
        ["4", "D", "d"],
        ["5", "E", "e"],
    ]



def test_lcd_display():
    calls = []

    class FakeLCD:
        def lcd_clear(self):
            calls.append("clear")

        def lcd_display_string(self, text, line):
            calls.append((text, line))

    main.lcd = FakeLCD()

    main.lcd_display("Temperature", "25 C")

    assert calls == [
        "clear",
        ("Temperature", 1),
        ("25 C", 2),
    ]



def test_key_pressed_adds_key():
    while not main.shared_keypad_queue.empty():
        main.shared_keypad_queue.get()

    main.key_pressed("5")

    assert main.shared_keypad_queue.get() == "5"



def test_start_keypad(monkeypatch):
    calls = []

    class FakeThread:
        def __init__(self, target, daemon):
            calls.append(("thread", target, daemon))

        def start(self):
            calls.append("started")

    monkeypatch.setattr(
        main.keypad,
        "init",
        lambda callback: calls.append(("init", callback)),
        raising=False,
    )
    monkeypatch.setattr(
        main.keypad,
        "get_key",
        lambda: None,
        raising=False,
    )
    monkeypatch.setattr(main.threading, "Thread", FakeThread)

    main.start_keypad()

    assert calls[0][0] == "init"
    assert calls[1] == ("thread", main.keypad.get_key, True)
    assert calls[2] == "started"



def test_process_keypad_ignores_key_when_no_alert():
    main.alert_active = False
    main.password_buffer = ""

    while not main.shared_keypad_queue.empty():
        main.shared_keypad_queue.get()

    main.shared_keypad_queue.put("5")
    main.process_keypad()

    assert main.password_buffer == ""



def test_process_keypad_wrong_password(monkeypatch):
    main.alert_active = True
    main.password_buffer = "9999"
    main.OVERRIDE_PASSWORD = "1234"

    while not main.shared_keypad_queue.empty():
        main.shared_keypad_queue.get()

    main.shared_keypad_queue.put("#")

    calls = []
    monkeypatch.setattr(
        main,
        "lcd_display",
        lambda a, b: calls.append((a, b)),
    )
    monkeypatch.setattr(main.time, "sleep", lambda _: None)

    main.process_keypad()

    assert main.password_buffer == ""
    assert ("Wrong Code", "Try Again") in calls



def test_process_keypad_correct_password(monkeypatch):
    main.alert_active = True
    main.password_buffer = "1234"
    main.OVERRIDE_PASSWORD = "1234"
    main.UNIT_ID = "UNIT01"

    while not main.shared_keypad_queue.empty():
        main.shared_keypad_queue.get()

    main.shared_keypad_queue.put("#")

    calls = []

    class FakeThread:
        def __init__(self, target, args=(), daemon=False):
            calls.append((target, args, daemon))

        def start(self):
            calls.append("started")

    monkeypatch.setattr(
        main,
        "handle_override_success",
        lambda method: calls.append(method),
    )
    monkeypatch.setattr(main.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        main,
        "timestamp",
        lambda: "2026-08-16 19:00:00",
    )

    main.process_keypad()

    assert "Keypad" in calls



def test_read_temperature_normal(monkeypatch):
    main.current_temp = 0.0

    monkeypatch.setattr(
        main.temp_humid_sensor,
        "read_temp_humidity",
        lambda: (25.5, 60.0),
        raising=False,
    )

    result = main.read_temperature()

    assert result == 25.5
    assert main.current_temp == 25.5



def test_read_temperature_sensor_failure(monkeypatch):
    main.current_temp = 25.0

    def fake_sensor():
        raise RuntimeError("Sensor error")

    monkeypatch.setattr(
        main.temp_humid_sensor,
        "read_temp_humidity",
        fake_sensor,
        raising=False,
    )

    result = main.read_temperature()

    assert result == 25.0



def test_read_light_normal(monkeypatch):
    main.current_light = 0

    monkeypatch.setattr(
        main.adc,
        "get_adc_value",
        lambda channel: 500,
        raising=False,
    )

    result = main.read_light()

    assert result == 500
    assert main.current_light == 500



def test_read_light_sensor_failure(monkeypatch):
    main.current_light = 250

    def fake_adc(channel):
        raise RuntimeError("ADC error")

    monkeypatch.setattr(
        main.adc,
        "get_adc_value",
        fake_adc,
        raising=False,
    )

    result = main.read_light()

    assert result == 250



def test_calibrate_light_calculates_average(monkeypatch):
    values = iter([100, 110, 120, 130, 140])

    monkeypatch.setattr(main, "read_light", lambda: next(values))
    monkeypatch.setattr(main.time, "sleep", lambda _: None)
    monkeypatch.setattr(main, "lcd_display", lambda a, b: None)
    monkeypatch.setattr(main, "write_log", lambda a, b="": None)

    main.calibrate_light()

    assert main.baseline_light == 120



def test_light_triggered_above_threshold():
    main.baseline_light = 100
    main.current_light = 141

    result = main.light_triggered()

    assert result is True



def test_light_not_triggered_below_threshold():
    main.baseline_light = 100
    main.current_light = 130

    result = main.light_triggered()

    assert result is False



def test_light_not_triggered_at_threshold():
    main.baseline_light = 100
    main.current_light = 140

    result = main.light_triggered()

    assert result is False



def test_sprinkler_on_sets_correct_angle(monkeypatch):
    calls = []

    monkeypatch.setattr(
        main.servo,
        "set_servo_position",
        lambda angle: calls.append(angle),
        raising=False,
    )
    monkeypatch.setattr(main, "lcd_display", lambda a, b: None)
    monkeypatch.setattr(main, "write_log", lambda a, b="": None)

    main.sprinkler_on()

    assert calls == [main.SPRINKLER_ON_ANGLE]



def test_sprinkler_off_sets_correct_angle(monkeypatch):
    calls = []

    monkeypatch.setattr(
        main.servo,
        "set_servo_position",
        lambda angle: calls.append(angle),
        raising=False,
    )
    monkeypatch.setattr(main, "write_log", lambda a, b="": None)

    main.sprinkler_off()

    assert calls == [main.SPRINKLER_OFF_ANGLE]



def test_update_local_alert_toggles_buzzer(monkeypatch):
    calls = []

    main.override_silence_until = 0
    main.override_was_silenced = False
    main.buzzer_state = False
    main.led_state = False
    main.last_buzzer_toggle = 0
    main.last_led_toggle = 100

    monkeypatch.setattr(main.time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(
        main.buzzer,
        "turn_on",
        lambda: calls.append("on"),
        raising=False,
    )
    monkeypatch.setattr(
        main.buzzer,
        "turn_off",
        lambda: calls.append("off"),
        raising=False,
    )
    monkeypatch.setattr(
        main.led,
        "set_output",
        lambda channel, value: None,
        raising=False,
    )

    main.update_local_alert()

    assert main.buzzer_state is True
    assert calls == ["on"]



def test_update_local_alert_override_silences_outputs(monkeypatch):
    calls = []

    main.override_silence_until = 100
    main.override_was_silenced = False

    monkeypatch.setattr(
        main.time,
        "monotonic",
        lambda: 50,
    )

    monkeypatch.setattr(
        main,
        "sprinkler_on",
        lambda: calls.append("sprinkler"),
    )

    main.update_local_alert()

    assert calls == []
    assert main.override_was_silenced is True



def test_update_local_alert_resumes_sprinkler_after_override(monkeypatch):
    calls = []

    main.override_silence_until = 100
    main.override_was_silenced = True

    main.buzzer_state = False
    main.led_state = False
    main.last_buzzer_toggle = 101
    main.last_led_toggle = 101

    monkeypatch.setattr(
        main.time,
        "monotonic",
        lambda: 101,
    )

    monkeypatch.setattr(
        main,
        "sprinkler_on",
        lambda: calls.append("sprinkler"),
    )

    monkeypatch.setattr(
        main.buzzer,
        "turn_on",
        lambda: calls.append("buzzer"),
        raising=False,
    )

    monkeypatch.setattr(
        main.buzzer,
        "turn_off",
        lambda: calls.append("buzzer_off"),
        raising=False,
    )

    monkeypatch.setattr(
        main.led,
        "set_output",
        lambda channel, value: None,
        raising=False,
    )

    main.update_local_alert()

    assert "sprinkler" in calls
    assert main.override_was_silenced is False



def test_stop_local_alert_turns_off_outputs(monkeypatch):
    calls = []

    monkeypatch.setattr(
        main.buzzer,
        "turn_off",
        lambda: calls.append("buzzer"),
        raising=False,
    )
    monkeypatch.setattr(
        main.led,
        "set_output",
        lambda channel, value: calls.append(("led", channel, value)),
        raising=False,
    )

    main.buzzer_state = True
    main.led_state = True

    main.stop_local_alert()

    assert calls == ["buzzer", ("led", 0, 0)]
    assert main.buzzer_state is False
    assert main.led_state is False



def test_init_camera(monkeypatch):
    calls = []

    class FakeCamera:
        def create_still_configuration(self, **kwargs):
            calls.append(("config", kwargs))
            return kwargs

        def configure(self, config):
            calls.append(("configure", config))

        def start(self):
            calls.append("start")

    monkeypatch.setattr(main, "Picamera2", FakeCamera)
    monkeypatch.setattr(main.time, "sleep", lambda _: None)
    monkeypatch.setattr(main, "write_log", lambda a, b="": None)

    main.init_camera()

    assert main.picam2 is not None
    assert calls[0][0] == "config"
    assert calls[1][0] == "configure"
    assert "start" in calls



def test_init_camera_failure(monkeypatch):
    calls = []

    class BadCamera:
        def __init__(self):
            raise RuntimeError("camera error")

    monkeypatch.setattr(main, "Picamera2", BadCamera)
    monkeypatch.setattr(
        main,
        "write_log",
        lambda event, details="": calls.append((event, details)),
    )

    main.init_camera()

    assert main.picam2 is None
    assert calls[0][0] == "CAMERA_INIT_ERROR"



def test_close_camera(monkeypatch):
    calls = []

    class FakeCamera:
        def stop(self):
            calls.append("stop")

        def close(self):
            calls.append("close")

    main.picam2 = FakeCamera()

    main.close_camera()

    assert calls == ["stop", "close"]
    assert main.picam2 is None



def test_close_camera_when_not_initialised():
    main.picam2 = None

    main.close_camera()

    assert main.picam2 is None



def test_capture_photo_success(tmp_path, monkeypatch):
    class FakeCamera:
        def capture_file(self, filename):
            Path(filename).write_bytes(b"fake-jpeg")

    main.picam2 = FakeCamera()
    main.PHOTO_DIR = tmp_path

    monkeypatch.setattr(main, "write_log", lambda a, b="": None)

    result = main.capture_photo()

    assert result is not None
    assert result.exists()
    assert result.read_bytes() == b"fake-jpeg"



def test_capture_photo_failure(tmp_path, monkeypatch):
    class BadCamera:
        def capture_file(self, filename):
            raise RuntimeError("camera error")

    main.picam2 = BadCamera()
    main.PHOTO_DIR = tmp_path

    logs = []
    monkeypatch.setattr(
        main,
        "write_log",
        lambda event, details="": logs.append((event, details)),
    )

    result = main.capture_photo()

    assert result is None
    assert logs[0][0] == "PHOTO_CAPTURE_ERROR"



def test_capture_photo_when_camera_not_initialised(monkeypatch):
    main.picam2 = None

    logs = []

    monkeypatch.setattr(
        main,
        "write_log",
        lambda event, details="": logs.append((event, details)),
    )

    result = main.capture_photo()

    assert result is None
    assert logs == [
        ("PHOTO_CAPTURE_ERROR", "Camera is not available.")
    ]



def test_send_telegram_message_not_configured(monkeypatch):
    main.TELEGRAM_BOT_TOKEN = ""
    main.TELEGRAM_CHAT_ID = ""

    result = main.send_telegram_message("Test")

    assert result is False



def test_send_telegram_message_success(monkeypatch):
    main.TELEGRAM_BOT_TOKEN = "fake-token"
    main.TELEGRAM_CHAT_ID = "fake-chat"

    class FakeResponse:
        status = 200

    monkeypatch.setattr(
        main.request,
        "urlopen",
        lambda request, timeout: FakeResponse(),
    )

    result = main.send_telegram_message("Test")

    assert result is True



def test_send_telegram_message_network_failure(monkeypatch):
    main.TELEGRAM_BOT_TOKEN = "fake-token"
    main.TELEGRAM_CHAT_ID = "fake-chat"

    def fake_urlopen(request, timeout):
        raise OSError("network error")

    monkeypatch.setattr(main.request, "urlopen", fake_urlopen)

    result = main.send_telegram_message("Test")

    assert result is False



def test_send_telegram_photo_not_configured(tmp_path):
    main.TELEGRAM_BOT_TOKEN = ""
    main.TELEGRAM_CHAT_ID = ""

    photo = tmp_path / "test.jpg"
    photo.write_bytes(b"jpg")

    result = main.send_telegram_photo(photo)

    assert result is False



def test_send_telegram_photo_success(tmp_path, monkeypatch):
    main.TELEGRAM_BOT_TOKEN = "token"
    main.TELEGRAM_CHAT_ID = "chat"

    photo = tmp_path / "fire.jpg"
    photo.write_bytes(b"fake-jpeg")

    class FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda *args, **kwargs: FakeResult(),
    )

    result = main.send_telegram_photo(photo)

    assert result is True



def test_send_telegram_photo_http_failure(tmp_path, monkeypatch):
    main.TELEGRAM_BOT_TOKEN = "token"
    main.TELEGRAM_CHAT_ID = "chat"

    photo = tmp_path / "fire.jpg"
    photo.write_bytes(b"fake-jpeg")

    class FakeResult:
        returncode = 1
        stdout = ""
        stderr = "HTTP error"

    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda *args, **kwargs: FakeResult(),
    )

    result = main.send_telegram_photo(photo)

    assert result is False



def test_alert_notification_worker_success(monkeypatch):
    main.notification_message = "Fire Alert"
    main.notification_pending = True
    main.alert_source = "Sensor Alert"

    logs = []

    monkeypatch.setattr(
        main,
        "send_telegram_message",
        lambda message: True,
    )
    monkeypatch.setattr(
        main,
        "write_log",
        lambda event, details="": logs.append((event, details)),
    )

    main.alert_notification_worker()

    assert main.notification_pending is False
    assert logs == [("NOTIFICATION_SENT", "Sensor Alert")]



def test_alert_notification_worker_failure(monkeypatch):
    main.notification_message = "Fire Alert"
    main.notification_pending = True

    monkeypatch.setattr(
        main,
        "send_telegram_message",
        lambda message: False,
    )

    main.alert_notification_worker()

    assert main.notification_pending is True



def test_one_shot_telegram_worker_success(monkeypatch):
    logs = []

    monkeypatch.setattr(
        main,
        "send_telegram_message",
        lambda message: True,
    )
    monkeypatch.setattr(
        main,
        "write_log",
        lambda event, details="": logs.append((event, details)),
    )

    main.one_shot_telegram_worker("Test", "TEST_SENT")

    assert logs == [("TEST_SENT", "Telegram sent")]



def test_one_shot_telegram_worker_failure(monkeypatch):
    logs = []

    monkeypatch.setattr(
        main,
        "send_telegram_message",
        lambda message: False,
    )
    monkeypatch.setattr(
        main,
        "write_log",
        lambda event, details="": logs.append((event, details)),
    )

    main.one_shot_telegram_worker("Test", "TEST_SENT")

    assert logs == []



def test_photo_upload_worker_success(monkeypatch, tmp_path):
    photo = tmp_path / "fire.jpg"
    photo.write_bytes(b"jpg")

    class StopWorker(Exception):
        pass

    class FakeQueue:
        def __init__(self):
            self.first = True

        def get(self):
            if self.first:
                self.first = False
                return photo
            raise StopWorker()

        def task_done(self):
            pass

    monkeypatch.setattr(main, "photo_upload_queue", FakeQueue())

    logs = []

    monkeypatch.setattr(
        main,
        "send_telegram_photo",
        lambda path: True,
    )
    monkeypatch.setattr(
        main,
        "write_log",
        lambda event, details="": logs.append((event, details)),
    )

    with pytest.raises(StopWorker):
        main.photo_upload_worker()

    assert logs == [("PHOTO_SENT", str(photo))]



def test_photo_upload_worker_skips_photo_when_alert_inactive(
    monkeypatch,
    tmp_path
):
    photo = tmp_path / "fire.jpg"
    photo.write_bytes(b"jpg")

    class StopWorker(Exception):
        pass

    class FakeQueue:
        def __init__(self):
            self.first = True

        def get(self):
            if self.first:
                self.first = False
                return photo
            raise StopWorker()

        def task_done(self):
            pass

    monkeypatch.setattr(main, "photo_upload_queue", FakeQueue())
    main.alert_active = False

    calls = []

    monkeypatch.setattr(
        main,
        "send_telegram_photo",
        lambda path: calls.append(path),
    )

    with pytest.raises(StopWorker):
        main.photo_upload_worker()

    assert calls == []



def test_build_alert_message_sensor(monkeypatch):
    main.UNIT_ID = "UNIT01"
    main.alert_source = "Sensor Alert"
    main.alert_reason = ["Temperature", "Light"]
    main.current_temp = 55.0
    main.current_light = 500

    monkeypatch.setattr(
        main,
        "timestamp",
        lambda: "2026-08-16 19:00:00",
    )

    result = main.build_alert_message()

    assert "SMART FIRE ALERT" in result
    assert "Unit: UNIT01" in result
    assert "Time: 2026-08-16 19:00:00" in result
    assert "Type: Sensor Alert" in result
    assert "Triggered Sensor: Temperature, Light" in result
    assert "Temperature: 55.0 C" in result
    assert "Light: 500" in result



def test_build_alert_message_manual(monkeypatch):
    main.UNIT_ID = "UNIT02"
    main.alert_source = "Manual Alert"
    main.alert_reason = ["Manual Button"]
    main.current_temp = 25.0
    main.current_light = 200

    monkeypatch.setattr(
        main,
        "timestamp",
        lambda: "2026-08-16 20:00:00",
    )

    result = main.build_alert_message()

    assert "Type: Manual Alert" in result
    assert "Triggered Sensor: Manual Button" in result



def test_rfid_authorised():
    main.AUTHORIZED_RFID_IDS = {"123456"}

    result = main.rfid_id_is_authorised("123456")

    assert result is True



def test_rfid_unauthorised():
    main.AUTHORIZED_RFID_IDS = {"123456"}

    result = main.rfid_id_is_authorised("999999")

    assert result is False



def test_rfid_none():
    result = main.rfid_id_is_authorised(None)

    assert result is False



def test_process_rfid_override_unauthorised(monkeypatch):
    class FakeRFID:
        def read_id_no_block(self):
            return "999999"

    main.alert_active = True
    main.rfid_reader = FakeRFID()
    main.AUTHORIZED_RFID_IDS = {"123456"}
    main._last_rfid_id = None
    main._last_rfid_time = 0

    logs = []

    monkeypatch.setattr(main.time, "monotonic", lambda: 100)
    monkeypatch.setattr(
        main,
        "write_log",
        lambda event, details="": logs.append((event, details)),
    )

    main.process_rfid_override()

    assert ("RFID_DENIED", "Unauthorised RFID card") in logs



def test_process_rfid_override_authorised(monkeypatch):
    class FakeRFID:
        def read_id_no_block(self):
            return "123456"

    main.alert_active = True
    main.rfid_reader = FakeRFID()
    main.AUTHORIZED_RFID_IDS = {"123456"}
    main._last_rfid_id = None
    main._last_rfid_time = 0

    calls = []

    monkeypatch.setattr(main.time, "monotonic", lambda: 100)
    monkeypatch.setattr(
        main,
        "write_log",
        lambda event, details="": calls.append((event, details)),
    )
    monkeypatch.setattr(
        main,
        "one_shot_telegram_worker",
        lambda *args: calls.append("telegram"),
    )
    monkeypatch.setattr(
        main,
        "handle_override_success",
        lambda method: calls.append(method),
    )
    monkeypatch.setattr(
        main,
        "timestamp",
        lambda: "2026-08-16 19:00:00",
    )

    main.process_rfid_override()

    assert ("RFID_OVERRIDE", "Authorised RFID override") in calls
    assert "telegram" in calls
    assert "RFID" in calls



def test_process_rfid_override_repeated_card(monkeypatch):
    class FakeRFID:
        def read_id_no_block(self):
            return "123456"

    main.alert_active = True
    main.rfid_reader = FakeRFID()
    main.AUTHORIZED_RFID_IDS = {"123456"}
    main._last_rfid_id = "123456"
    main._last_rfid_time = 100

    monkeypatch.setattr(main.time, "monotonic", lambda: 101)
    monkeypatch.setattr(
        main,
        "handle_override_success",
        lambda method: pytest.fail("Repeated card should be ignored"),
    )

    main.process_rfid_override()



def test_handle_override_success_keypad(monkeypatch):
    calls = []

    monkeypatch.setattr(main.time, "monotonic", lambda: 100)
    monkeypatch.setattr(
        main,
        "lcd_display",
        lambda a, b: calls.append((a, b)),
    )
    monkeypatch.setattr(
        main,
        "stop_local_alert",
        lambda: calls.append("stop"),
    )
    monkeypatch.setattr(
        main,
        "sprinkler_off",
        lambda: calls.append("sprinkler"),
    )
    monkeypatch.setattr(
        main,
        "write_log",
        lambda a, b="": calls.append((a, b)),
    )

    main.handle_override_success("Keypad")

    assert main.override_silence_until == 100 + main.OVERRIDE_DELAY
    assert ("Override", "Accepted") in calls
    assert "stop" in calls
    assert "sprinkler" in calls



def test_handle_override_success_resets_rfid(monkeypatch):
    main._last_rfid_id = "123456"
    main._last_rfid_time = 100

    monkeypatch.setattr(main.time, "monotonic", lambda: 200)
    monkeypatch.setattr(main, "lcd_display", lambda a, b: None)
    monkeypatch.setattr(main, "stop_local_alert", lambda: None)
    monkeypatch.setattr(main, "sprinkler_off", lambda: None)
    monkeypatch.setattr(main, "write_log", lambda a, b="": None)

    main.handle_override_success("RFID")

    assert main._last_rfid_id is None
    assert main._last_rfid_time == 0.0



def test_start_alert_sensor(monkeypatch):
    calls = []

    main.alert_active = False

    monkeypatch.setattr(
        main,
        "build_alert_message",
        lambda: "ALERT",
    )
    monkeypatch.setattr(
        main,
        "lcd_display",
        lambda a, b: calls.append((a, b)),
    )
    monkeypatch.setattr(
        main,
        "write_log",
        lambda a, b="": calls.append((a, b)),
    )
    monkeypatch.setattr(
        main,
        "sprinkler_on",
        lambda: calls.append("sprinkler"),
    )

    main.start_alert("Sensor Alert", ["Temperature"])

    assert main.alert_active is True
    assert main.alert_source == "Sensor Alert"
    assert main.alert_reason == ["Temperature"]
    assert main.notification_message == "ALERT"
    assert main.notification_pending is True
    assert ("FIRE DETECTED", "Evacuate Now!") in calls
    assert "sprinkler" in calls



def test_start_alert_manual(monkeypatch):
    calls = []

    main.alert_active = False

    monkeypatch.setattr(
        main,
        "build_alert_message",
        lambda: "MANUAL",
    )
    monkeypatch.setattr(
        main,
        "lcd_display",
        lambda a, b: calls.append((a, b)),
    )
    monkeypatch.setattr(
        main,
        "write_log",
        lambda a, b="": None,
    )
    monkeypatch.setattr(
        main,
        "sprinkler_on",
        lambda: calls.append("sprinkler"),
    )

    main.start_alert("Manual Alert", ["Manual Button"])

    assert main.alert_active is True
    assert main.alert_source == "Manual Alert"
    assert main.alert_reason == ["Manual Button"]
    assert ("Manual Alert", "Help Requested") in calls
    assert "sprinkler" in calls



def test_start_alert_does_not_restart_existing_alert(monkeypatch):
    main.alert_active = True
    main.alert_source = "Existing Alert"

    monkeypatch.setattr(
        main,
        "sprinkler_on",
        lambda: pytest.fail("Should not restart alert"),
    )

    main.start_alert("Sensor Alert", ["Temperature"])

    assert main.alert_source == "Existing Alert"



def test_clear_alert_resets_state(monkeypatch):
    calls = []

    main.alert_active = True
    main.alert_source = "Sensor Alert"
    main.alert_reason = ["Temperature"]
    main.notification_pending = True
    main.override_silence_until = 100
    main.override_was_silenced = True
    main._last_rfid_id = "123"
    main._last_rfid_time = 50

    monkeypatch.setattr(
        main,
        "stop_local_alert",
        lambda: calls.append("stop"),
    )
    monkeypatch.setattr(
        main,
        "sprinkler_off",
        lambda: calls.append("sprinkler"),
    )
    monkeypatch.setattr(
        main,
        "write_log",
        lambda a, b="": calls.append((a, b)),
    )

    main.clear_alert("Test clear")

    assert main.alert_active is False
    assert main.alert_source == ""
    assert main.alert_reason == []
    assert main.notification_pending is False
    assert main.override_silence_until == 0.0
    assert main.override_was_silenced is False
    assert main._last_rfid_id is None
    assert main._last_rfid_time == 0.0
    assert "stop" in calls
    assert "sprinkler" in calls



def test_clear_alert_logs_reason(monkeypatch):
    logs = []

    monkeypatch.setattr(main, "stop_local_alert", lambda: None)
    monkeypatch.setattr(main, "sprinkler_off", lambda: None)
    monkeypatch.setattr(
        main,
        "write_log",
        lambda event, details="": logs.append((event, details)),
    )

    main.clear_alert("Manual switch turned OFF")

    assert logs == [
        ("ALERT_CLEARED", "Manual switch turned OFF")
    ]



def test_monitor_sensors_normal(monkeypatch):
    main.alert_active = False
    main.temp_count = 0
    main.light_count = 0
    main.current_temp = 25.0
    main.current_light = 100
    main.baseline_light = 100

    monkeypatch.setattr(main, "read_temperature", lambda: 25.0)
    monkeypatch.setattr(main, "read_light", lambda: 100)
    monkeypatch.setattr(
        main,
        "start_alert",
        lambda *args: pytest.fail("No alert expected"),
    )

    main.monitor_sensors()

    assert main.temp_count == 0
    assert main.light_count == 0



def test_monitor_sensors_temperature_alert_after_three(monkeypatch):
    main.alert_active = False
    main.temp_count = 2
    main.light_count = 0
    main.current_temp = 55.0
    main.current_light = 100
    main.baseline_light = 100

    calls = []

    monkeypatch.setattr(main, "read_temperature", lambda: 55.0)
    monkeypatch.setattr(main, "read_light", lambda: 100)
    monkeypatch.setattr(
        main,
        "start_alert",
        lambda source, reasons: calls.append((source, reasons)),
    )

    main.monitor_sensors()

    assert main.temp_count == 3
    assert calls == [("Sensor Alert", ["Temperature"])]



def test_monitor_sensors_light_alert_after_three(monkeypatch):
    main.alert_active = False
    main.temp_count = 0
    main.light_count = 2
    main.current_temp = 25.0
    main.current_light = 150
    main.baseline_light = 100

    calls = []

    monkeypatch.setattr(main, "read_temperature", lambda: 25.0)
    monkeypatch.setattr(main, "read_light", lambda: 150)
    monkeypatch.setattr(
        main,
        "start_alert",
        lambda source, reasons: calls.append((source, reasons)),
    )

    main.monitor_sensors()

    assert main.light_count == 3
    assert calls == [("Sensor Alert", ["Light"])]



def test_monitor_sensors_both_alert(monkeypatch):
    main.alert_active = False
    main.temp_count = 2
    main.light_count = 2
    main.current_temp = 55.0
    main.current_light = 150
    main.baseline_light = 100

    calls = []

    monkeypatch.setattr(main, "read_temperature", lambda: 55.0)
    monkeypatch.setattr(main, "read_light", lambda: 150)
    monkeypatch.setattr(
        main,
        "start_alert",
        lambda source, reasons: calls.append((source, reasons)),
    )

    main.monitor_sensors()

    assert calls == [
        ("Sensor Alert", ["Temperature", "Light"])
    ]



def test_monitor_sensors_clears_sensor_alert_after_three_normal(monkeypatch):
    main.alert_active = True
    main.alert_source = "Sensor Alert"
    main.clear_count = 2
    main.current_temp = 25.0
    main.current_light = 100
    main.baseline_light = 100

    calls = []

    monkeypatch.setattr(main, "read_temperature", lambda: 25.0)
    monkeypatch.setattr(main, "read_light", lambda: 100)
    monkeypatch.setattr(
        main,
        "clear_alert",
        lambda reason: calls.append(reason),
    )

    main.monitor_sensors()

    assert main.clear_count == 3
    assert calls == ["Sensors normal for 3 readings"]



def test_process_alert_when_no_alert(monkeypatch):
    main.alert_active = False

    calls = []

    monkeypatch.setattr(
        main,
        "process_keypad",
        lambda: calls.append("keypad"),
    )
    monkeypatch.setattr(
        main,
        "update_local_alert",
        lambda: calls.append("local"),
    )
    monkeypatch.setattr(
        main,
        "process_rfid_override",
        lambda: calls.append("rfid"),
    )
    monkeypatch.setattr(main.time, "monotonic", lambda: 0)

    main.process_alert()

    assert calls == ["keypad", "local", "rfid"]



def test_process_alert_sends_pending_notification(monkeypatch):
    main.alert_active = True
    main.notification_pending = True
    main.last_notification_attempt = 0
    main.last_photo_capture = 100

    calls = []

    monkeypatch.setattr(main.time, "monotonic", lambda: 1)
    monkeypatch.setattr(main, "process_keypad", lambda: None)
    monkeypatch.setattr(main, "update_local_alert", lambda: None)
    monkeypatch.setattr(main, "process_rfid_override", lambda: None)

    class FakeThread:
        def __init__(self, target, daemon):
            calls.append((target, daemon))

        def start(self):
            calls.append("started")

    monkeypatch.setattr(main.threading, "Thread", FakeThread)

    main.process_alert()

    assert calls[0] == (main.alert_notification_worker, True)
    assert calls[1] == "started"



def test_init_hardware(monkeypatch):
    calls = []

    class FakeLCD:
        def lcd_clear(self):
            calls.append("lcd_clear")

    class FakeRFID:
        pass

    monkeypatch.setattr(
        main.led,
        "init",
        lambda: calls.append("led"),
        raising=False,
    )
    monkeypatch.setattr(
        main.adc,
        "init",
        lambda: calls.append("adc"),
        raising=False,
    )
    monkeypatch.setattr(
        main.buzzer,
        "init",
        lambda: calls.append("buzzer"),
        raising=False,
    )
    monkeypatch.setattr(
        main.input_switch,
        "init",
        lambda: calls.append("switch"),
        raising=False,
    )
    monkeypatch.setattr(
        main.servo,
        "init",
        lambda: calls.append("servo"),
        raising=False,
    )
    monkeypatch.setattr(
        main.temp_humid_sensor,
        "init",
        lambda: calls.append("temp"),
        raising=False,
    )
    monkeypatch.setattr(
        main.rfid_reader_hal,
        "init",
        lambda: calls.append("rfid"),
        raising=False,
    )
    monkeypatch.setattr(
        main.rfid_reader_hal,
        "SimpleMFRC522",
        FakeRFID,
        raising=False,
    )
    monkeypatch.setattr(
        main.LCD,
        "lcd",
        lambda: FakeLCD(),
        raising=False,
    )
    monkeypatch.setattr(
        main,
        "start_keypad",
        lambda: calls.append("keypad"),
    )
    monkeypatch.setattr(
        main.servo,
        "set_servo_position",
        lambda angle: calls.append(("servo_angle", angle)),
        raising=False,
    )
    monkeypatch.setattr(
        main.buzzer,
        "turn_off",
        lambda: calls.append("buzzer_off"),
        raising=False,
    )
    monkeypatch.setattr(
        main.led,
        "set_output",
        lambda channel, value: calls.append(("led", channel, value)),
        raising=False,
    )
    monkeypatch.setattr(
        main,
        "init_camera",
        lambda: calls.append("camera"),
    )
    monkeypatch.setattr(
        main,
        "lcd_display",
        lambda a, b: calls.append(("display", a, b)),
    )
    monkeypatch.setattr(
        main,
        "write_log",
        lambda a, b="": None,
    )

    main.init_hardware()

    assert "led" in calls
    assert "adc" in calls
    assert "buzzer" in calls
    assert "switch" in calls
    assert "servo" in calls
    assert "temp" in calls
    assert "rfid" in calls
    assert "keypad" in calls
    assert "camera" in calls



def test_init_hardware_rfid_failure(monkeypatch):
    class BadRFID:
        def __init__(self):
            raise RuntimeError("RFID error")

    calls = []

    monkeypatch.setattr(main.led, "init", lambda: None, raising=False)
    monkeypatch.setattr(main.adc, "init", lambda: None, raising=False)
    monkeypatch.setattr(main.buzzer, "init", lambda: None, raising=False)
    monkeypatch.setattr(main.input_switch, "init", lambda: None, raising=False)
    monkeypatch.setattr(main.servo, "init", lambda: None, raising=False)
    monkeypatch.setattr(
        main.temp_humid_sensor,
        "init",
        lambda: None,
        raising=False,
    )
    monkeypatch.setattr(
        main.rfid_reader_hal,
        "init",
        lambda: None,
        raising=False,
    )
    monkeypatch.setattr(
        main.rfid_reader_hal,
        "SimpleMFRC522",
        BadRFID,
        raising=False,
    )
    monkeypatch.setattr(
        main.LCD,
        "lcd",
        lambda: types.SimpleNamespace(lcd_clear=lambda: None),
        raising=False,
    )
    monkeypatch.setattr(main, "start_keypad", lambda: None)
    monkeypatch.setattr(
        main.servo,
        "set_servo_position",
        lambda angle: None,
        raising=False,
    )
    monkeypatch.setattr(
        main.buzzer,
        "turn_off",
        lambda: None,
        raising=False,
    )
    monkeypatch.setattr(
        main.led,
        "set_output",
        lambda channel, value: None,
        raising=False,
    )
    monkeypatch.setattr(main, "init_camera", lambda: None)
    monkeypatch.setattr(main, "lcd_display", lambda a, b: None)
    monkeypatch.setattr(
        main,
        "write_log",
        lambda event, details="": calls.append((event, details)),
    )

    main.init_hardware()

    assert main.rfid_reader is None
    assert calls[0][0] == "RFID_INIT_ERROR"



def test_start_photo_worker(monkeypatch):
    calls = []

    class FakeThread:
        def __init__(self, target, daemon):
            calls.append((target, daemon))

        def start(self):
            calls.append("started")

    monkeypatch.setattr(main.threading, "Thread", FakeThread)

    main.start_photo_worker()

    assert calls == [
        (main.photo_upload_worker, True),
        "started",
    ]

