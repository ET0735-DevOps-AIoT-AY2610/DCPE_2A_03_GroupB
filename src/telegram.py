"""
PART 3B - TELEGRAM / NETWORK COMMUNICATION

Purpose:
- Load .env credentials
- Telegram text notification
- Telegram photo upload
- Retry workers
- Photo upload queue
- Alert message construction
- Communication event logging

This part can upload photos that were captured by Part 3A.
"""

import os
import csv
import time
import queue
import threading
from datetime import datetime
from pathlib import Path
from urllib import request, parse


# ---------------- CONFIGURATION ----------------

def load_env_file():
    """Load local project credentials from .env without requiring python-dotenv."""
    env_file = Path(".env")

    if not env_file.exists():
        return

    try:
        with env_file.open("r") as file:
            for line in file:
                line = line.strip()

                if not line or line.startswith("#") or "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()

                if len(value) >= 2 and (
                    (value[0] == '"' and value[-1] == '"') or
                    (value[0] == "'" and value[-1] == "'")
                ):
                    value = value[1:-1]

                os.environ[key] = value
    except OSError as exc:
        print("Environment file warning:", exc)


load_env_file()

UNIT_ID = os.getenv("FIRE_UNIT_ID", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

NOTIFICATION_RETRY = 10.0

LOG_FILE = Path("communication_events.csv")

photo_upload_queue = queue.Queue()


# ---------------- GLOBAL STATE ----------------

current_temp = 0.0
current_light = 0

alert_source = "Sensor Alert"
alert_reason = ["Temperature"]


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


# ---------------- TELEGRAM MESSAGE ----------------

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram is not configured.")
        return False

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_BOT_TOKEN
        + "/sendMessage"
    )

    data = parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }).encode()

    req = request.Request(url, data=data)

    try:
        response = request.urlopen(req, timeout=8)
        return response.status == 200

    except Exception as exc:
        print("Telegram notification failed:", exc)
        return False


# ---------------- TELEGRAM PHOTO UPLOAD ----------------

def send_telegram_photo(photo_path):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram is not configured.")
        return False

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_BOT_TOKEN
        + "/sendPhoto"
    )

    boundary = "----SmartFireBoundary"
    body = bytearray()

    def add_text_field(name, value):
        body.extend(("--" + boundary + "\r\n").encode())
        body.extend(
            (
                'Content-Disposition: form-data; '
                'name="{}"\r\n\r\n'
            ).format(name).encode()
        )
        body.extend(str(value).encode())
        body.extend(b"\r\n")

    add_text_field("chat_id", TELEGRAM_CHAT_ID)
    add_text_field(
        "caption",
        "Fire Alert Photo\n"
        "Unit: {}\n"
        "Time: {}".format(UNIT_ID, timestamp())
    )

    try:
        photo_data = Path(photo_path).read_bytes()
    except OSError as exc:
        print("Unable to read photo for Telegram:", exc)
        return False

    body.extend(("--" + boundary + "\r\n").encode())
    body.extend(
        (
            'Content-Disposition: form-data; '
            'name="photo"; filename="{}"\r\n'
        ).format(Path(photo_path).name).encode()
    )
    body.extend(b"Content-Type: image/jpeg\r\n\r\n")
    body.extend(photo_data)
    body.extend(b"\r\n")
    body.extend(("--" + boundary + "--\r\n").encode())

    req = request.Request(
        url,
        data=bytes(body),
        headers={
            "Content-Type":
                "multipart/form-data; boundary=" + boundary
        }
    )

    try:
        response = request.urlopen(req, timeout=8)
        return response.status == 200

    except Exception as exc:
        print("Telegram photo upload failed:", exc)
        return False


# ---------------- WORKERS / ALERT MESSAGE ----------------

def alert_notification_worker(message):
    if send_telegram_message(message):
        write_log("NOTIFICATION_SENT", "Telegram text sent")


def one_shot_telegram_worker(message, log_event=None):
    if send_telegram_message(message) and log_event:
        write_log(log_event, "Telegram sent")


def photo_upload_worker():
    while True:
        photo_path = photo_upload_queue.get()

        try:
            while True:
                if send_telegram_photo(photo_path):
                    write_log("PHOTO_SENT", str(photo_path))
                    break

                time.sleep(NOTIFICATION_RETRY)

        finally:
            photo_upload_queue.task_done()


def build_alert_message():
    return (
        "SMART FIRE ALERT\n"
        "Unit: {}\n"
        "Time: {}\n"
        "Type: {}\n"
        "Triggered Sensor: {}\n"
        "Temperature: {:.1f} C\n"
        "Light: {}"
    ).format(
        UNIT_ID,
        timestamp(),
        alert_source,
        ", ".join(alert_reason),
        current_temp,
        current_light
    )


# ---------------- STANDALONE MAIN ----------------

def main():
    print("Starting standalone Telegram system...")

    worker = threading.Thread(
        target=photo_upload_worker,
        daemon=True
    )
    worker.start()

    message = build_alert_message()

    print("Sending test alert notification...")
    alert_notification_worker(message)

    print("Telegram system running.")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nTelegram test stopped.")


if __name__ == "__main__":
    main()
