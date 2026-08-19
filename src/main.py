import os
import csv
import time
import queue
import threading
import subprocess
from datetime import datetime
from pathlib import Path
from urllib import request, parse

from picamera2 import Picamera2

from hal import hal_led as led
from hal import hal_lcd as LCD
from hal import hal_adc as adc
from hal import hal_buzzer as buzzer
from hal import hal_keypad as keypad
from hal import hal_input_switch as input_switch
from hal import hal_servo as servo
from hal import hal_temp_humidity_sensor as temp_humid_sensor
from hal import hal_rfid_reader as rfid_reader_hal


# ================================================================
# SRS REQUIREMENT TRACEABILITY
# ================================================================
# REQ-01: Read DHT11 temperature every 3 seconds.
# REQ-02: Define 35 C as MAX_TEMP; >35 C is a possible fire condition.
# REQ-03: Temperature > 35 C for 3 consecutive readings sets tempAlert.
# REQ-04: Display current temperature on the LCD in monitoring mode.
# REQ-05: Read LDR on ADC channel 0 every 3 seconds.
# REQ-06: Establish the LDR baseline during startup calibration.
# REQ-07: Flag possible flame/fire when LDR rises >40% above baseline.
# REQ-08: LDR condition for 3 consecutive readings sets lightAlert.
# REQ-09: Display current LDR reading on the LCD in monitoring mode.
# REQ-10: Continuously evaluate temperature and light alert conditions.
# REQ-11: Either confirmed sensor alert enters the fireDetected state.
# REQ-12: Display FIRE DETECTED / Evacuate Now!
# REQ-13: Log triggered sensors with the Raspberry Pi System Clock.
# REQ-14: Buzzer pulses 500 ms ON / 500 ms OFF while alert is active.
# REQ-15: Red LED flashes at 2 Hz while alert is active.
# REQ-16: Continuous audible alarm pattern; acoustic coverage is hardware-tested.
# REQ-17: Send the alert through Wi-Fi/network within 10 seconds when available.
# REQ-18: Include unit ID, timestamp, triggered sensor/manual input, temperature
#         and light readings in the alert notification.
# REQ-19: Capture fire-scene photos approximately every 5 seconds.
# REQ-20: Save each photo locally and attempt Telegram transmission.
# REQ-21: Send each alert notification to the configured Telegram chat.
# REQ-22: Retry failed notification/photo transmission every 10 seconds without
#         interrupting the buzzer, LED, or sprinkler.
# REQ-23: Provide a physical manual alert switch.
# REQ-24: Manual alert follows the same alert sequence as automatic detection.
# REQ-25: Manual alert bypasses the 3-reading sensor confirmation.
# REQ-26: Display Manual Alert / Help Requested.
# REQ-27: Activate the sprinkler valve through the servo after fire confirmation.
# REQ-28: Sensor alerts clear after authorised override or 3 normal readings;
#         manual alerts clear when the switch is OFF or by authorised override.
# REQ-29: Display Sprinkler ON on the LCD during sprinkler activation.
# REQ-30: Log sprinkler activation/deactivation using the System Clock.
# REQ-31: Keypad provides an authorised password override.
# REQ-32: Display Enter Override / Code during keypad override.
# REQ-33: Correct password deactivates buzzer, LED and sprinkler and sends the
#         False Alarm --- Overridden notification.
# REQ-34: RFID provides an optional authorised override.
# REQ-35: Valid RFID override performs the same override actions.
# REQ-36: Timestamp all events with the Raspberry Pi System Clock.
# REQ-37: Keep the latest 100 event records in the local event log.
# REQ-38: Detect and trigger an alert within 10 seconds of a threshold breach.
# REQ-39: Transmit the SCDF/alert notification within 10 seconds when network
#         conditions permit.
# REQ-40: Use at least two independent sensor types: DHT11 and LDR.
# REQ-41: Store override credentials securely and never display them on LCD.
# REQ-42: Only authorised users with valid credentials can override an alert.
# REQ-43: Provide clear audible, flashing LED and LCD indications for elderly
#         residents.
# REQ-44: Use a modular HAL-based design for sensors and actuators.
# ================================================================


# ================================================================
# .ENV LOADER / CREDENTIALS
# ================================================================

# Copy the example environment file to create the actual .env file.
# .env.example is a template containing the required variable names,
# while .env will contain our actual private values.
# cp .env.example .env

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

# Values stored in .env. Do not hard-code these credentials in the source.
UNIT_ID = os.getenv("FIRE_UNIT_ID", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
OVERRIDE_PASSWORD = os.getenv("FIRE_OVERRIDE_PASSWORD", "")

# RFID override is optional in the SRS. If RFID is used, authorised IDs may
# additionally be supplied through FIRE_AUTHORIZED_RFID_IDS.
# Example: FIRE_AUTHORIZED_RFID_IDS=977573770339
AUTHORIZED_RFID_IDS = {
    item.strip()
    for item in os.getenv("FIRE_AUTHORIZED_RFID_IDS", "").split(",")
    if item.strip()
}

MAX_TEMP = 35.0
LIGHT_DELTA = 0.40
CONSECUTIVE_LIMIT = 3

SENSOR_INTERVAL = 3.0
NOTIFICATION_RETRY = 10.0
PHOTO_INTERVAL = 10.0
OVERRIDE_DELAY = 10.0

LDR_CHANNEL = 0
SPRINKLER_ON_ANGLE = 180
SPRINKLER_OFF_ANGLE = 0

LOG_FILE = Path("fire_events.csv")
PHOTO_DIR = Path("fire_photos")
LOG_LIMIT = 100

RFID_REPEAT_DELAY = 2.0


# ================================================================
# GLOBAL STATE
# ================================================================

lcd = None
picam2 = None
rfid_reader = None

shared_keypad_queue = queue.Queue()
photo_upload_queue = queue.Queue()

alert_active = False
alert_source = ""
alert_reason = []

current_temp = 0.0
current_light = 0
baseline_light = 0.0

temp_count = 0
light_count = 0
clear_count = 0

buzzer_state = False
led_state = False
last_buzzer_toggle = 0.0
last_led_toggle = 0.0

notification_pending = False
notification_message = ""
last_notification_attempt = 0.0
last_photo_capture = 0.0
override_silence_until = 0.0
override_was_silenced = False

password_buffer = ""

_last_rfid_id = None
_last_rfid_time = 0.0


# ================================================================
# BASIC FUNCTIONS / EVENT LOGGING
# ================================================================

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
    """Keep the header plus only the newest LOG_LIMIT records."""
    if not LOG_FILE.exists():
        return

    try:
        with LOG_FILE.open("r", newline="") as file:
            rows = list(csv.reader(file))

        if not rows:
            return

        header = rows[0]
        data_rows = rows[1:]

        if len(data_rows) <= LOG_LIMIT:
            return

        with LOG_FILE.open("w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(header)
            writer.writerows(data_rows[-LOG_LIMIT:])

    except (OSError, csv.Error) as exc:
        print("CSV trim warning:", exc)


def lcd_display(line1, line2):
    try:
        lcd.lcd_clear()
        lcd.lcd_display_string(str(line1)[:16], 1)
        lcd.lcd_display_string(str(line2)[:16], 2)
    except Exception as exc:
        print("LCD warning:", exc)


# ================================================================
# KEYPAD OVERRIDE
# ================================================================

def key_pressed(key):
    shared_keypad_queue.put(key)


def start_keypad():
    try:
        keypad.init(key_pressed)

        keypad_thread = threading.Thread(target=keypad.get_key, daemon=True)
        keypad_thread.start()
    except Exception as exc:
        print("Keypad initialisation failed:", exc)
        write_log("KEYPAD_INIT_ERROR", str(exc))


def process_keypad():
    """Process override keys without blocking the main alert loop."""
    global password_buffer

    while not shared_keypad_queue.empty():
        key = str(shared_keypad_queue.get())

        if not alert_active:
            continue

        if key == "*":
            # Start a new override entry only if one is not already in progress.
            # This prevents repeated '*' events from clearing the password
            # buffer while the user is entering the code.
            if not password_buffer:
                lcd_display("Enter Override", "Code")

        elif key == "#":
            if OVERRIDE_PASSWORD and password_buffer == OVERRIDE_PASSWORD:
                handle_override_success("Keypad")

                threading.Thread(
                    target=one_shot_telegram_worker,
                    args=(
                        "False Alarm --- Overridden\n"
                        "Unit: {}\n"
                        "Time: {}\n"
                        "Method: Keypad".format(UNIT_ID, timestamp()),
                        "OVERRIDE_NOTIFICATION_SENT"
                    ),
                    daemon=True
                ).start()
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


# ================================================================
# TEMPERATURE + LDR
# ================================================================

def read_temperature():
    global current_temp

    try:
        temperature, _humidity = temp_humid_sensor.read_temp_humidity()

        # Ignore invalid DHT11 readings
        if temperature is None or temperature < -40 or temperature > 80:
            print("Invalid temperature reading:", temperature)
            write_log(
                "TEMPERATURE_READ_ERROR",
                "Invalid temperature={}".format(temperature)
            )
            return current_temp

        current_temp = temperature
        return current_temp

    except Exception as exc:
        print("Temperature sensor warning:", exc)
        write_log("TEMPERATURE_READ_ERROR", str(exc))
        return current_temp


def read_light():
    global current_light

    try:
        current_light = adc.get_adc_value(LDR_CHANNEL)
        return current_light
    except Exception as exc:
        print("LDR warning:", exc)
        return current_light


def calibrate_light():
    global baseline_light

    samples = []
    lcd_display("LDR Calibration", "Keep room normal")
    print("Calibrating LDR baseline...")

    for count in range(5):
        value = read_light()
        samples.append(value)
        print("LDR sample {} = {}".format(count + 1, value))
        time.sleep(1)

    baseline_light = sum(samples) / len(samples)

    print("LDR baseline =", baseline_light)
    print("LDR threshold =", baseline_light * (1 + LIGHT_DELTA))

    write_log("LDR_BASELINE", "baseline={}".format(baseline_light))


def light_triggered():
    threshold = baseline_light * (1 + LIGHT_DELTA)
    return current_light > threshold


# ================================================================
# SPRINKLER
# ================================================================

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


# ================================================================
# BUZZER + LED
# ================================================================

def update_local_alert():
    """Update local alarm outputs without blocking the alert loop."""
    global buzzer_state
    global led_state
    global last_buzzer_toggle
    global last_led_toggle
    global override_was_silenced

    now = time.monotonic()

    if now < override_silence_until:
        override_was_silenced = True
        return

    if override_was_silenced:
        sprinkler_on()
        override_was_silenced = False

    # 500 ms ON / 500 ms OFF = 1 complete cycle per second.
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

    # 250 ms ON / 250 ms OFF = 2 Hz.
    if now - last_led_toggle >= 0.25:
        led_state = not led_state

        try:
            led.set_output(0, 1 if led_state else 0)
        except Exception as exc:
            print("LED warning:", exc)
        last_led_toggle = now


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


# ================================================================
# PICAMERA2
# ================================================================

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
    if picam2 is None:
        print("Photo capture skipped: camera is not available.")
        write_log("PHOTO_CAPTURE_ERROR", "Camera is not available.")
        return None
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
    except (OSError, RuntimeError) as exc:
        print("Photo capture failed:", exc)
        write_log("PHOTO_CAPTURE_ERROR", str(exc))
        return None


def close_camera():
    global picam2

    if picam2 is not None:
        try:
            picam2.stop()
        except Exception as exc:
            print("Camera stop warning:", exc)

        try:
            picam2.close()
        except Exception as exc:
            print("Camera close warning:", exc)

        picam2 = None


# ================================================================
# TELEGRAM
# ================================================================

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


def send_telegram_photo(photo_path):
    """Upload a captured photo to Telegram using Python urllib."""
    try:
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            print("Telegram is not configured.")
            return False

        url = (
            "https://api.telegram.org/bot"
            + TELEGRAM_BOT_TOKEN
            + "/sendPhoto"
        )

        boundary = "----AIoTFireAlertBoundary"
        boundary_bytes = boundary.encode()

        with open(photo_path, "rb") as photo_file:
            photo_data = photo_file.read()

        body = (
            b"--" + boundary_bytes + b"\r\n"
            b'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
            + TELEGRAM_CHAT_ID.encode()
            + b"\r\n"
            b"--" + boundary_bytes + b"\r\n"
            b'Content-Disposition: form-data; name="photo"; filename="fire.jpg"\r\n'
            b"Content-Type: image/jpeg\r\n\r\n"
            + photo_data
            + b"\r\n"
            b"--" + boundary_bytes + b"--\r\n"
        )

        req = request.Request(
            url,
            data=body,
            headers={
                "Content-Type": (
                    "multipart/form-data; boundary=" + boundary
                )
            },
            method="POST",
        )

        response = request.urlopen(req, timeout=30)
        response_data = response.read().decode()

        if response.status == 200:
            print("Telegram photo uploaded successfully.")
            return True

        print("Telegram photo upload failed:", response_data)
        return False

    except Exception as exc:
        print("Telegram photo upload failed:", exc)
        return False


def alert_notification_worker():
    """Retry the current alert notification until it succeeds."""
    global notification_pending

    message = notification_message

    if send_telegram_message(message):
        notification_pending = False
        write_log("NOTIFICATION_SENT", alert_source)
    else:
        # Leave notification_pending=True. process_alert() will retry.
        pass


def one_shot_telegram_worker(message, log_event=None):
    """Send a non-critical message without blocking local alert control."""
    if send_telegram_message(message) and log_event:
        write_log(log_event, "Telegram sent")


def photo_upload_worker():
    """Upload queued photos; retry each failed upload every 10 seconds."""
    while True:
        photo_path = photo_upload_queue.get()

        if not alert_active:
            photo_upload_queue.task_done()
            continue

        try:
            while True:
                if not alert_active:
                    break
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


# ================================================================
# RFID OVERRIDE
# ================================================================

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

    one_shot_telegram_worker(
        "False Alarm --- Overridden\n"
        "Unit: {}\n"
        "Time: {}\n"
        "Method: RFID".format(UNIT_ID, timestamp()),
        "OVERRIDE_NOTIFICATION_SENT"
    )

    handle_override_success("RFID")


# ================================================================
# FIRE ALERT
# ================================================================

def handle_override_success(method):
    global _last_rfid_id
    global _last_rfid_time
    global override_silence_until

    # Reset RFID repeat protection so the same authorised card
    # can be used again on a later alert.
    _last_rfid_id = None
    _last_rfid_time = 0.0

    lcd_display("Override", "Accepted")

    override_silence_until = time.monotonic() + OVERRIDE_DELAY

    stop_local_alert()
    sprinkler_off()

    write_log(
        "OVERRIDE_ACCEPTED",
        "{} override accepted. Local alarm silenced for {} seconds".format(
            method,
            int(OVERRIDE_DELAY)
        )
    )

    print(
        "{} override accepted. Local alarm silenced for {} seconds.".format(
            method,
            int(OVERRIDE_DELAY)
        )
    )


def start_alert(source, reasons):
    global alert_active
    global alert_source
    global alert_reason
    global notification_pending
    global notification_message
    global last_notification_attempt
    global last_photo_capture
    global password_buffer
    global override_silence_until
    global temp_count
    global light_count
    global clear_count
    global override_was_silenced

    if alert_active:
        return

    alert_active = True
    alert_source = source
    alert_reason = reasons[:]

    notification_message = build_alert_message()
    notification_pending = True
    last_notification_attempt = 0.0
    last_photo_capture = 0.0
    override_silence_until = 0.0
    override_was_silenced = False

    password_buffer = ""
    temp_count = 0
    light_count = 0
    clear_count = 0

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
    """Clear the active alert and return local outputs to the safe state."""
    global alert_active, alert_source, alert_reason
    global notification_pending
    global _last_rfid_id, _last_rfid_time
    global override_silence_until
    global override_was_silenced

    while not photo_upload_queue.empty():
        try:
            photo_upload_queue.get_nowait()
            photo_upload_queue.task_done()
        except queue.Empty:
            break

    alert_active = False
    alert_source = ""
    alert_reason = []

    notification_pending = False
    override_silence_until = 0.0
    override_was_silenced = False

    # Reset RFID repeat protection so the same card can be used
    # again when the next alert occurs.
    _last_rfid_id = None
    _last_rfid_time = 0.0

    stop_local_alert()
    sprinkler_off()

    write_log("ALERT_CLEARED", reason)
    print("Alert cleared:", reason)

def monitor_sensors():
    global temp_count
    global light_count
    global clear_count

    read_temperature()
    read_light()

    temp_over_limit = current_temp > MAX_TEMP
    light_bad = light_triggered()

    if temp_over_limit:
        temp_count += 1
    else:
        temp_count = 0

    if light_bad:
        light_count += 1
    else:
        light_count = 0

    temp_alert = temp_count >= CONSECUTIVE_LIMIT
    light_alert = light_count >= CONSECUTIVE_LIMIT

    if not alert_active and (temp_alert or light_alert):
        reasons = []

        if temp_alert:
            reasons.append("Temperature")

        if light_alert:
            reasons.append("Light")

        start_alert("Sensor Alert", reasons)
        return

    # REQ-28: sensor-triggered alerts clear only when BOTH readings
    # are below their respective fire thresholds for 3 readings.
    if alert_active and alert_source == "Sensor Alert":
        if not temp_over_limit and not light_bad:
            clear_count += 1
        else:
            clear_count = 0

        if clear_count >= CONSECUTIVE_LIMIT:
            clear_alert("Sensors normal for 3 readings")


# ================================================================
# ACTIVE ALERT PROCESSING
# ================================================================

def process_alert():
    global last_notification_attempt
    global last_photo_capture
    now = time.monotonic()

    # Local controls are always processed before any network operation.
    process_keypad()
    update_local_alert()
    process_rfid_override()

    if not alert_active:
        return

    # ------------------------------------------------------------
    # TELEGRAM TEXT NOTIFICATION
    # ------------------------------------------------------------
    if (
        notification_pending
        and (
            last_notification_attempt == 0.0
            or now - last_notification_attempt >= NOTIFICATION_RETRY
        )
    ):
        last_notification_attempt = now

        threading.Thread(
            target=alert_notification_worker,
            daemon=True
        ).start()

    # ------------------------------------------------------------
    # PHOTO CAPTURE
    # ------------------------------------------------------------
    if now - last_photo_capture >= PHOTO_INTERVAL:
        photo_path = capture_photo()
        last_photo_capture = now

        if photo_path is not None:
            photo_upload_queue.put(photo_path)



# ================================================================
# HARDWARE INITIALISATION
# ================================================================

def init_hardware():
    global lcd
    global rfid_reader

    print("Initialising Smart Fire Alert System...")

    led.init()
    adc.init()
    buzzer.init()
    input_switch.init()
    servo.init()
    temp_humid_sensor.init()

    try:
        rfid_reader_hal.init()
        rfid_reader = rfid_reader_hal.SimpleMFRC522()
        write_log("RFID_READY", "MFRC522 reader initialised")
        print("RFID reader ready.")
    except Exception as exc:
        rfid_reader = None
        write_log("RFID_INIT_ERROR", str(exc))
        print("RFID initialisation failed:", exc)

    lcd = LCD.lcd()
    lcd.lcd_clear()

    start_keypad()

    servo.set_servo_position(SPRINKLER_OFF_ANGLE)
    buzzer.turn_off()
    led.set_output(0, 0)

    init_camera()

    lcd_display("Smart Fire Alert", "Starting...")


def start_photo_worker():
    worker = threading.Thread(
        target=photo_upload_worker,
        daemon=True
    )
    worker.start()


# ================================================================
# MAIN
# ================================================================

def main():
    try:
        init_hardware()
        start_photo_worker()

        time.sleep(2)
        calibrate_light()

        lcd_display("System Ready", "Monitoring...")

        write_log("SYSTEM_START", "Unit {}".format(UNIT_ID))

        next_sensor_read = time.monotonic()

        while True:
            now = time.monotonic()

            # ------------------------------------------------------------
            # Manual emergency switch
            # ------------------------------------------------------------
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

            # ------------------------------------------------------------
            # Sensor monitoring
            # ------------------------------------------------------------
            if now >= next_sensor_read:
                try:
                    monitor_sensors()
                except Exception as exc:
                    print("Sensor monitoring warning:", exc)
                    write_log("SENSOR_ERROR", str(exc))

                if not alert_active:
                    lcd_display(
                        "Temp: {:.1f} C".format(current_temp),
                        "Light: {}".format(current_light)
                    )

                next_sensor_read = now + SENSOR_INTERVAL

            # ------------------------------------------------------------
            # Active alert processing
            # ------------------------------------------------------------
            if alert_active:
                try:
                    process_alert()
                except Exception as exc:
                    print("Alert processing warning:", exc)
                    write_log("ALERT_PROCESSING_ERROR", str(exc))

            time.sleep(0.05)

    except KeyboardInterrupt:
        print("Smart Fire Alert System stopped.")

    finally:
        close_camera()


if __name__ == "__main__":
    main()
