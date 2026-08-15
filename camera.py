"""
PART 3A - CAMERA / PHOTO CAPTURE

Purpose:
- Picamera2 initialisation
- Fire-scene photo capture
- Local photo storage

Credentials are not required for this part.
"""

import csv
import time
from datetime import datetime
from pathlib import Path

from picamera2 import Picamera2


# ---------------- CONFIGURATION ----------------

PHOTO_INTERVAL = 5.0
LOG_FILE = Path("camera_events.csv")
PHOTO_DIR = Path("fire_photos")


# ---------------- GLOBAL STATE ----------------

picam2 = None


# ---------------- LOGGING ----------------

def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_log(event, details=""):
    new_file = not LOG_FILE.exists()

    try:
        with LOG_FILE.open("a", newline="") as file:
            writer = csv.writer(file)

            if new_file:
                writer.writerow(["timestamp", "event", "details"])

            writer.writerow([timestamp(), event, details])

        trim_event_log()

    except (OSError, csv.Error) as exc:
        print("Log warning:", exc)


def trim_event_log():
    if not LOG_FILE.exists():
        return

    try:
        with LOG_FILE.open("r", newline="") as file:
            rows = list(csv.reader(file))

        if not rows:
            return

        header = rows[0]
        data_rows = rows[1:]

        if len(data_rows) <= 100:
            return

        with LOG_FILE.open("w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(header)
            writer.writerows(data_rows[-100:])

    except (OSError, csv.Error) as exc:
        print("CSV trim warning:", exc)


# ---------------- CAMERA ----------------

def init_camera():
    global picam2

    try:
        picam2 = Picamera2()

        camera_config = picam2.create_still_configuration(
            main={"size": (1920, 1080)},
            lores={"size": (640, 480)},
            display="lores"
        )

        picam2.configure(camera_config)
        picam2.start()
        time.sleep(2)

        print("Picamera2 started.")

    except Exception as exc:
        picam2 = None
        print("Camera initialisation failed:", exc)
        write_log("CAMERA_INIT_ERROR", str(exc))


def capture_photo():
    try:
        PHOTO_DIR.mkdir(exist_ok=True)

        filename = PHOTO_DIR / (
            "fire_{}.jpg".format(
                datetime.now().strftime("%Y%m%d_%H%M%S")
            )
        )

        picam2.capture_file(str(filename))

        print("Photo captured:", filename)
        write_log("PHOTO_CAPTURED", str(filename))

        return filename

    except (OSError, RuntimeError, AttributeError) as exc:
        print("Photo capture failed:", exc)
        write_log("PHOTO_CAPTURE_ERROR", str(exc))
        return None


# ---------------- STANDALONE MAIN ----------------

def main():
    print("Starting standalone camera system...")

    init_camera()

    if picam2 is None:
        print("Camera unavailable. Exiting.")
        return

    print("Camera system running.")
    print("A photo will be captured every {} seconds.".format(PHOTO_INTERVAL))
    print("Press Ctrl+C to stop.")

    last_photo = 0.0

    try:
        while True:
            now = time.monotonic()

            if now - last_photo >= PHOTO_INTERVAL:
                capture_photo()
                last_photo = now

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nCamera test stopped.")


if __name__ == "__main__":
    main()
