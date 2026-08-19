"""
LOCAL ALERT + OVERRIDE

Purpose:
- Manual emergency switch
- Keypad password override
- RFID override
- Buzzer
- Red LED
- Sprinkler servo
- 10-second override silence period
"""

import os
import csv
import time
import queue
import threading
from datetime import datetime
from pathlib import Path

from hal import hal_led as led
from hal import hal_lcd as LCD
from hal import hal_buzzer as buzzer
from hal import hal_keypad as keypad
from hal import hal_input_switch as input_switch
from hal import hal_servo as servo
from hal import hal_rfid_reader as rfid_reader_hal


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

OVERRIDE_PASSWORD = os.getenv("FIRE_OVERRIDE_PASSWORD", "")

AUTHORIZED_RFID_IDS = {
    item.strip()
    for item in os.getenv("FIRE_AUTHORIZED_RFID_IDS", "").split(",")
    if item.strip()
}

SPRINKLER_ON_ANGLE = 180
SPRINKLER_OFF_ANGLE = 0
OVERRIDE_DELAY = 10.0
RFID_REPEAT_DELAY = 2.0

LOG_FILE = Path("local_alert_events.csv")


# ---------------- GLOBAL STATE ----------------

lcd = None
rfid_reader = None

shared_keypad_queue = queue.Queue()

alert_active = False
alert_source = ""
alert_reason = []

override_silence_until = 0.0

password_buffer = ""

buzzer_state = False
led_state = False
last_buzzer_toggle = 0.0
last_led_toggle = 0.0

_last_rfid_id = None
_last_rfid_time = 0.0


# ---------------- LOGGING / LCD ----------------

def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_log(event, details=""):
    try:
        new_file = not LOG_FILE.exists()

        with LOG_FILE.open("a", newline="") as file:
            writer = csv.writer(file)

            if new_file:
                writer.writerow(["timestamp", "event", "details"])

            writer.writerow([timestamp(), event, details])

    except (OSError, csv.Error) as exc:
        print("Log warning:", exc)


def lcd_display(line1, line2):
    try:
        lcd.lcd_clear()
        lcd.lcd_display_string(str(line1)[:16], 1)
        lcd.lcd_display_string(str(line2)[:16], 2)
    except Exception as exc:
        print("LCD warning:", exc)


# ---------------- SPRINKLER / LOCAL OUTPUTS ----------------

def sprinkler_on():
    try:
        servo.set_servo_position(SPRINKLER_ON_ANGLE)
    except Exception as exc:
        print("Sprinkler ON warning:", exc)

    lcd_display(
        "Sprinkler ON",
        "Angle: {} deg".format(SPRINKLER_ON_ANGLE)
    )

    write_log(
        "SPRINKLER_ON",
        "angle={}".format(SPRINKLER_ON_ANGLE)
    )


def sprinkler_off():
    try:
        servo.set_servo_position(SPRINKLER_OFF_ANGLE)
    except Exception as exc:
        print("Sprinkler OFF warning:", exc)

    write_log(
        "SPRINKLER_OFF",
        "angle={}".format(SPRINKLER_OFF_ANGLE)
    )


def stop_local_alert():
    global buzzer_state
    global led_state

    try:
        buzzer.turn_off()
    except Exception as exc:
        print("Buzzer stop warning:", exc)

    try:
        led.set_output(0, 0)
    except Exception as exc:
        print("LED stop warning:", exc)

    buzzer_state = False
    led_state = False


def stop_local_alert_outputs():
    stop_local_alert()


def update_local_alert():
    global buzzer_state
    global led_state
    global last_buzzer_toggle
    global last_led_toggle

    if time.monotonic() < override_silence_until:
        stop_local_alert_outputs()
        sprinkler_off()
        return

    now = time.monotonic()

    if now - last_buzzer_toggle >= 0.5:
        buzzer_state = not buzzer_state

        try:
            if buzzer_state:
                buzzer.turn_on()
            else:
                buzzer.turn_off()
        except Exception as exc:
            print("Buzzer warning:", exc)

        last_buzzer_toggle = now

    if now - last_led_toggle >= 0.25:
        led_state = not led_state

        try:
            led.set_output(0, 1 if led_state else 0)
        except Exception as exc:
            print("LED warning:", exc)

        last_led_toggle = now


# ---------------- ALERT CONTROL ----------------

def start_alert(source, reasons):
    global alert_active
    global alert_source
    global alert_reason
    global password_buffer

    if alert_active:
        return

    alert_active = True
    alert_source = source
    alert_reason = reasons[:]
    password_buffer = ""

    if source == "Manual Alert":
        lcd_display("Manual Alert", "Help Requested")
    else:
        lcd_display("FIRE DETECTED", "Evacuate Now!")

    write_log(
        "ALERT_TRIGGERED",
        "{} | {}".format(source, ", ".join(reasons))
    )

    sprinkler_on()


def clear_alert(reason):
    global alert_active
    global alert_source
    global alert_reason
    global override_silence_until
    global _last_rfid_id
    global _last_rfid_time

    alert_active = False
    alert_source = ""
    alert_reason = []

    override_silence_until = 0.0

    _last_rfid_id = None
    _last_rfid_time = 0.0

    stop_local_alert_outputs()
    sprinkler_off()

    write_log("ALERT_CLEARED", reason)
    print("Alert cleared:", reason)


# ---------------- KEYPAD OVERRIDE ----------------

def key_pressed(key):
    shared_keypad_queue.put(key)


def start_keypad():
    try:
        keypad.init(key_pressed)

        keypad_thread = threading.Thread(
            target=keypad.get_key,
            daemon=True
        )
        keypad_thread.start()

    except Exception as exc:
        print("Keypad initialisation failed:", exc)
        write_log("KEYPAD_INIT_ERROR", str(exc))


def handle_override_success(method):
    global override_silence_until
    global _last_rfid_id
    global _last_rfid_time

    override_silence_until = time.monotonic() + OVERRIDE_DELAY

    _last_rfid_id = None
    _last_rfid_time = 0.0

    lcd_display("Override", "Accepted")

    stop_local_alert()
    sprinkler_off()

    write_log(
        "OVERRIDE_ACCEPTED",
        "{} override accepted; local alarm silenced for 10 seconds".format(method)
    )

    print(
        "{} override accepted. Local alarm silenced for 10 seconds.".format(method)
    )


def process_keypad():
    global password_buffer

    while not shared_keypad_queue.empty():
        key = str(shared_keypad_queue.get())

        if not alert_active:
            continue

        if key == "*":
            if not password_buffer:
                lcd_display("Enter Override", "Code")

        elif key == "#":
            if OVERRIDE_PASSWORD and password_buffer == OVERRIDE_PASSWORD:
                handle_override_success("Keypad")
                password_buffer = ""
            else:
                password_buffer = ""
                lcd_display("Wrong Code", "Try Again")
                time.sleep(0.2)

                if alert_active:
                    lcd_display("Enter Override", "Code")

        elif key.isdigit():
            if len(password_buffer) < 12:
                password_buffer += key

                lcd_display(
                    "Enter Override",
                    "*" * len(password_buffer)
                )


# ---------------- RFID OVERRIDE ----------------

def rfid_id_is_authorised(card_id):
    if card_id is None:
        return False

    return str(card_id).strip() in AUTHORIZED_RFID_IDS


def process_rfid_override():
    global _last_rfid_id
    global _last_rfid_time

    if not alert_active or rfid_reader is None:
        return

    try:
        card_id = rfid_reader.read_id_no_block()
    except Exception as exc:
        write_log("RFID_ERROR", str(exc))
        return

    if card_id is None:
        return

    card_id_text = str(card_id).strip()
    now = time.monotonic()

    if (
        card_id_text == _last_rfid_id
        and now - _last_rfid_time < RFID_REPEAT_DELAY
    ):
        return

    _last_rfid_id = card_id_text
    _last_rfid_time = now

    if not rfid_id_is_authorised(card_id_text):
        write_log("RFID_DENIED", "Unauthorised RFID card")
        return

    write_log("RFID_OVERRIDE", "Authorised RFID override")
    handle_override_success("RFID")


# ---------------- STANDALONE MAIN ----------------

def main():
    global lcd
    global rfid_reader

    print("Starting standalone local alert/override system...")

    led.init()
    buzzer.init()
    input_switch.init()
    servo.init()

    try:
        rfid_reader_hal.init()
        rfid_reader = rfid_reader_hal.SimpleMFRC522()
        print("RFID reader ready.")
    except Exception as exc:
        rfid_reader = None
        print("RFID initialisation failed:", exc)

    lcd = LCD.lcd()
    lcd.lcd_clear()

    start_keypad()

    servo.set_servo_position(SPRINKLER_OFF_ANGLE)
    buzzer.turn_off()
    led.set_output(0, 0)

    lcd_display("System Ready", "Monitoring...")

    while True:
        try:
            switch_on = input_switch.read_slide_switch()
        except Exception as exc:
            print("Manual switch warning:", exc)
            switch_on = False

        if switch_on:
            if not alert_active:
                start_alert("Manual Alert", ["Manual Button"])
        else:
            if alert_active and alert_source == "Manual Alert":
                clear_alert("Manual switch turned OFF")

        if alert_active:
            process_keypad()
            process_rfid_override()
            update_local_alert()

        time.sleep(0.05)


if __name__ == "__main__":
    main()
