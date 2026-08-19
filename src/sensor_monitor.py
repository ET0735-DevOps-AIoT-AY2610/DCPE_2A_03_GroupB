"""
SENSOR MONITORING

Purpose:
- DHT11 temperature monitoring
- LDR baseline calibration
- 3-consecutive-reading fire detection
- LCD display
- Local event logging
"""


import os
import csv
import time
from datetime import datetime
from pathlib import Path

from hal import hal_lcd as LCD
from hal import hal_adc as adc
from hal import hal_temp_humidity_sensor as temp_humid_sensor


# ---------------- CONFIGURATION ----------------

TEMP_WARNING = 35.0
MAX_TEMP = 50.0
LIGHT_DELTA = 0.40
CONSECUTIVE_LIMIT = 3
SENSOR_INTERVAL = 3.0
LDR_CHANNEL = 0

LOG_FILE = Path("sensor_events.csv")
LOG_LIMIT = 100


# ---------------- GLOBAL STATE ----------------

lcd = None

current_temp = 0.0
current_light = 0
baseline_light = 0.0

temp_count = 0
light_count = 0
clear_count = 0

alert_active = False
alert_reason = []


# ---------------- EVENT LOGGING ----------------

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

        if len(data_rows) <= LOG_LIMIT:
            return

        with LOG_FILE.open("w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(header)
            writer.writerows(data_rows[-LOG_LIMIT:])

    except (OSError, csv.Error) as exc:
        print("CSV trim warning:", exc)


# ---------------- LCD ----------------

def lcd_display(line1, line2):
    try:
        lcd.lcd_clear()
        lcd.lcd_display_string(str(line1)[:16], 1)
        lcd.lcd_display_string(str(line2)[:16], 2)
    except Exception as exc:
        print("LCD warning:", exc)


# ---------------- SENSOR FUNCTIONS ----------------

def read_temperature():
    global current_temp

    try:
        temperature, _humidity = temp_humid_sensor.read_temp_humidity()
        current_temp = temperature
        return current_temp
    except Exception as exc:
        print("Temperature sensor warning:", exc)
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


def monitor_sensors():
    global temp_count
    global light_count
    global clear_count
    global alert_active
    global alert_reason

    read_temperature()
    read_light()

    temp_bad = current_temp > MAX_TEMP
    light_bad = light_triggered()

    if temp_bad:
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
        alert_reason = []

        if temp_alert:
            alert_reason.append("Temperature")

        if light_alert:
            alert_reason.append("Light")

        alert_active = True

        lcd_display("FIRE DETECTED", "Evacuate Now!")
        write_log(
            "ALERT_TRIGGERED",
            "Sensor Alert | {}".format(", ".join(alert_reason))
        )

        print("FIRE DETECTED:", ", ".join(alert_reason))

    elif alert_active:
        if not temp_bad and not light_bad:
            clear_count += 1
        else:
            clear_count = 0

        if clear_count >= CONSECUTIVE_LIMIT:
            alert_active = False
            alert_reason = []
            clear_count = 0
            lcd_display("System Ready", "Monitoring...")
            write_log("ALERT_CLEARED", "Sensors normal for 3 readings")
            print("Alert cleared: Sensors normal for 3 readings")


# ---------------- HARDWARE SETUP / MAIN ----------------

def main():
    global lcd

    print("Starting standalone sensor monitoring...")

    adc.init()
    temp_humid_sensor.init()

    lcd = LCD.lcd()
    lcd.lcd_clear()

    calibrate_light()

    lcd_display("System Ready", "Monitoring...")
    write_log("SYSTEM_START", "Standalone sensor monitor")

    next_sensor_read = time.monotonic()

    while True:
        now = time.monotonic()

        if now >= next_sensor_read:
            monitor_sensors()

            if not alert_active:
                lcd_display(
                    "Temp: {:.1f} C".format(current_temp),
                    "Light: {}".format(current_light)
                )

            next_sensor_read = now + SENSOR_INTERVAL

        time.sleep(0.05)


if __name__ == "__main__":
    main()
