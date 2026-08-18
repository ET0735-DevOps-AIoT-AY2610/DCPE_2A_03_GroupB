# AIoT Smart Fire Alert System

## Overview

The **AIoT Smart Fire Alert System** is a Raspberry Pi-based fire monitoring and alert system developed for the ET0735 DevOps-AIoT project.

The system continuously monitors the environment using a **DHT11 temperature sensor** and an **LDR light sensor**. When a fire condition is detected, it activates local alarms, controls the sprinkler, sends a Telegram alert, and captures fire-scene photos. Manual alerts and authorised keypad/RFID overrides are also supported.

The project is implemented in Python and uses a modular HAL (Hardware Abstraction Layer) for Raspberry Pi hardware.

## Main Features

- Continuous DHT11 temperature monitoring every **3 seconds**
- Continuous LDR monitoring every **3 seconds**
- LDR baseline calibration during startup
- Fire detection using independent temperature and light conditions
- Three consecutive abnormal sensor readings before a sensor alert is triggered
- LCD fire and system-status messages
- Buzzer alarm with a **500 ms ON / 500 ms OFF** pattern
- Red LED flashing at **2 Hz**
- Automatic sprinkler control through a servo
- Manual emergency alert switch
- Telegram text notifications
- Fire-scene photo capture at approximately **10-second intervals**
- Local timestamped photo storage
- Telegram photo upload with retry handling
- Failed Telegram notification/photo transmission retry every **10 seconds**
- Keypad password override
- Optional RFID override
- Event logging with Raspberry Pi system timestamps
- Local event log limited to the latest **100 records**
- Environment-variable based configuration for credentials

## System Behaviour

### Automatic Fire Detection

The system reads the DHT11 and LDR every 3 seconds.

A temperature condition is triggered when:

```text
Temperature > 35°C
```

An LDR condition is triggered when:

```text
LDR reading > 40% above the calibrated baseline
```

Each condition must occur for **3 consecutive readings** before its corresponding alert is confirmed.

If either sensor condition is confirmed:

1. A sensor alert is started.
2. The LCD displays `FIRE DETECTED` and `Evacuate Now!`.
3. The buzzer and red LED are activated.
4. The sprinkler servo is activated.
5. A Telegram alert is queued for transmission.
6. Fire-scene photo capture begins.
7. Events are recorded in the local CSV log.

### Manual Emergency Alert

The physical manual alert switch can start an alert without waiting for the three-reading sensor confirmation.

The LCD displays:

```text
Manual Alert
Help Requested
```

The manual alert follows the same main alert sequence, including local alarm, sprinkler activation, Telegram notification and photo capture.

Turning the manual switch OFF clears a manual alert.

### Alert Clearing and Override

For a sensor-triggered alert, the system clears the alert when **both sensor readings are normal for 3 consecutive readings**.

An authorised keypad password or RFID card can also override an active alert.

A successful override:

- silences the local buzzer/LED,
- turns the sprinkler off,
- records the override event, and
- sends a `False Alarm --- Overridden` Telegram notification.

The current implementation uses an **override delay of 10 seconds** for the local alarm suppression behaviour.

## Telegram and Photo Handling

The system sends alert messages to the configured Telegram chat.

Alert messages contain:

- Unit ID
- Timestamp
- Alert type
- Triggered sensor/manual input
- Current temperature
- Current light reading

While an alert is active, the camera captures fire-scene photos approximately every **10 seconds**.

Photos are saved locally using timestamped filenames such as:

```text
fire_YYYYMMDD_HHMMSS.jpg
```

Captured photos are placed into an upload queue and handled by a background worker.

If a photo upload fails, the worker waits **10 seconds** before retrying. The local alarm and sprinkler processing are kept separate from the network upload operation.

## Configuration

The project uses a local `.env` file for credentials and configuration.

Create the file from the provided example:

```bash
cp .env.example .env
```

Then configure:

```text
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
FIRE_UNIT_ID=HDB-01
FIRE_OVERRIDE_PASSWORD=your_password_here
FIRE_AUTHORIZED_RFID_IDS=your_rfid_id_here
```

**Do not commit `.env` to GitHub.** The repository's `.gitignore` excludes `.env` files.

The `.env.example` file contains only placeholder values and is safe to keep in the repository.

## Project Structure

```text
DCPE_2A_03_GroupB/
│
├── docs/
│   ├── SRS_AIoT_Smart_Fire_Alert_System.docx
│   ├── Sprint_Planning_Template.xlsx
│   └── System_Test_Report_template.xlsx
│
├── src/
│   ├── hal/                         # HAL Git submodule
│   │
│   ├── main.py                      # Main Raspberry Pi application
│   ├── camera.py                    # Camera-related module
│   ├── local_alert_override.py      # Local alert override module
│   ├── sensor_monitor.py            # Sensor monitoring module
│   ├── telegram.py                  # Telegram module
│   │
│   ├── test_main.py                 # Pytest tests for main.py
│   ├── test_camera.py               # Camera tests
│   ├── test_local_alert_override.py # Override tests
│   ├── test_sensor_monitor.py       # Sensor-monitor tests
│   └── test_telegram.py             # Telegram tests
│
├── .env.example                     # Configuration template
├── .gitignore
├── .gitmodules
└── README.md
```

> The `src/hal` directory is maintained as a Git submodule and points to the project's HAL repository.

## Running the Application on Raspberry Pi

From the project directory:

```bash
cd src
```

Make sure the required hardware is connected and the local `.env` file has been configured.

Run:

```bash
python3 main.py
```

The application initialises the hardware, calibrates the LDR baseline, and then enters continuous monitoring mode.

To stop the application:

```text
Ctrl+C
```

The camera is closed when the application exits.

## Running the Pytest Tests

The test suite is designed so that `main.py` can be imported without actual Raspberry Pi hardware. Hardware modules such as Picamera2 and the HAL components are replaced with test doubles where required.

From `src`:

```bash
pytest test_main.py
```

To run the complete test suite:

```bash
pytest
```

For a more detailed result:

```bash
pytest -v
```

The `test_main.py` suite covers areas including:

- environment-file loading
- timestamp and event logging
- LCD output
- keypad input and password override
- temperature and LDR reading
- LDR calibration and thresholds
- sprinkler control
- buzzer and LED behaviour
- camera initialisation and photo capture
- Telegram message/photo handling
- notification retry behaviour
- photo upload queue behaviour
- RFID authorisation and override
- alert start/clear behaviour
- sensor confirmation and clearing
- active-alert processing
- hardware initialisation

## Hardware / Software Components

### Hardware

- Raspberry Pi
- DHT11 temperature/humidity sensor
- LDR connected through ADC channel 0
- LCD
- Buzzer
- Red LED
- Manual alert switch
- Servo motor for sprinkler valve
- Keypad
- RFID reader
- Raspberry Pi camera

### Software

- Python
- Pytest
- Picamera2
- Raspberry Pi HAL modules
- Telegram Bot API
- `curl` for Telegram photo uploads
- Git / GitHub

## Requirements Traceability

The implementation contains an SRS traceability section in `main.py` covering **REQ-01 through REQ-44**.

The requirements cover:

| Area | Requirements |
|---|---|
| Temperature detection | REQ-01 – REQ-04 |
| LDR detection | REQ-05 – REQ-09 |
| Fire trigger logic | REQ-10 – REQ-13 |
| Local alarm | REQ-14 – REQ-16 |
| Telegram notification and photos | REQ-17 – REQ-22 |
| Manual alert | REQ-23 – REQ-26 |
| Sprinkler control | REQ-27 – REQ-30 |
| Keypad/RFID override | REQ-31 – REQ-35 |
| Event logging | REQ-36 – REQ-37 |
| Performance | REQ-38 – REQ-39 |
| Security | REQ-40 – REQ-42 |
| Usability and maintainability | REQ-43 – REQ-44 |

## Important Timing Values

The current implementation uses:

| Function | Timing |
|---|---:|
| Sensor reading interval | 3 seconds |
| Temperature confirmation | 3 consecutive readings |
| LDR confirmation | 3 consecutive readings |
| Photo capture interval | 10 seconds |
| Failed transmission retry | 10 seconds |
| Telegram text request timeout | 8 seconds |
| Telegram photo connection timeout | 10 seconds |
| Telegram photo maximum request time | 30 seconds |
| Override delay | 10 seconds |
| Event log limit | 100 records |

## Security

Credentials are loaded from `.env` rather than hard-coded into the application source.

The following sensitive values should remain private:

- Telegram bot token
- Telegram chat ID
- Override password
- Authorised RFID IDs

Only `.env.example` should be committed to the repository.

## Project Documentation

The `docs/` directory contains the project's main documentation:

- **Software Requirements Specification (SRS)** – system requirements and design reference
- **Sprint Planning Template** – project planning documentation
- **System Test Report Template** – system-level testing documentation

