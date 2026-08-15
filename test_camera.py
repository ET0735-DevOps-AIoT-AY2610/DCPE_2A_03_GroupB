import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest


BASE = Path(__file__).resolve().parent


@pytest.fixture
def app(monkeypatch, tmp_path):
    """Import camera.py with Picamera2 mocked."""
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
    monkeypatch.chdir(tmp_path)

    spec = importlib.util.spec_from_file_location(
        "camera_test_app",
        BASE / "camera.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["part3_camera_test_app"] = module
    spec.loader.exec_module(module)

    return module


def test_constants(app):
    assert app.PHOTO_INTERVAL == 5.0
    assert app.LOG_FILE == Path("camera_events.csv")
    assert app.PHOTO_DIR == Path("fire_photos")


def test_init_camera_starts_camera(app):
    app.init_camera()

    assert app.picam2 is not None
    assert hasattr(app.picam2, "config")


def test_init_camera_failure_sets_camera_to_none(app, monkeypatch):
    class BrokenCamera:
        def __init__(self):
            raise RuntimeError("camera failure")

    monkeypatch.setattr(app, "Picamera2", BrokenCamera)
    app.write_log = Mock()

    app.init_camera()

    assert app.picam2 is None
    app.write_log.assert_called_once()
    assert app.write_log.call_args.args[0] == "CAMERA_INIT_ERROR"


def test_capture_photo_creates_jpeg(app, tmp_path):
    app.PHOTO_DIR = tmp_path / "fire_photos"
    app.picam2 = app.Picamera2()
    app.picam2.configure({})
    app.picam2.start()
    app.write_log = Mock()

    result = app.capture_photo()

    assert result is not None
    assert result.exists()
    assert result.suffix == ".jpg"
    assert result.read_bytes() == b"fake-jpeg"


def test_capture_photo_failure_returns_none(app):
    class BrokenCamera:
        def capture_file(self, filename):
            raise RuntimeError("capture failure")

    app.picam2 = BrokenCamera()
    app.write_log = Mock()

    result = app.capture_photo()

    assert result is None
    app.write_log.assert_called_once()
    assert app.write_log.call_args.args[0] == "PHOTO_CAPTURE_ERROR"


def test_event_log_is_trimmed_to_100_records(app):
    app.LOG_FILE = Path("camera_events.csv")

    app.write_log("TEST", "record")

    for i in range(105):
        app.write_log("TEST", str(i))

    with app.LOG_FILE.open("r", newline="") as file:
        rows = file.readlines()

    assert len(rows) == 101


def test_standalone_main_exits_when_camera_unavailable(app, monkeypatch):
    app.init_camera = Mock()
    app.picam2 = None

    app.main()

    app.init_camera.assert_called_once()


def test_photo_filename_uses_fire_prefix(app, tmp_path):
    app.PHOTO_DIR = tmp_path / "fire_photos"
    app.picam2 = app.Picamera2()
    app.write_log = Mock()

    result = app.capture_photo()

    assert result.name.startswith("fire_")
    assert result.name.endswith(".jpg")
