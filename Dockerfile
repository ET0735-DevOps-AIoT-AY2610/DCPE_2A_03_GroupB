FROM arm32v7/debian:bullseye-slim

WORKDIR /aiot-fire-app

# Add Raspberry Pi repository
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && wget -qO - https://archive.raspberrypi.org/debian/raspberrypi.gpg.key | apt-key add - \
    && echo "deb http://archive.raspberrypi.org/debian/ bullseye main" \
        > /etc/apt/sources.list.d/raspi.list \
    && apt-get update

# Install Python, Raspberry Pi camera stack and required system libraries
RUN apt-get install -y \
    python3 \
    python3-pip \
    python3-picamera2 \
    python3-libcamera \
    python3-kms++ \
    libcamera0 \
    libcamera-apps \
    python3-pyqt5 \
    python3-numpy \
    gcc \
    g++ \
    make \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip3 install --no-cache-dir -r requirements.txt

COPY spi.cpython-39-arm-linux-gnueabihf.so /usr/local/lib/python3.9/dist-packages/

COPY ./src ./src

CMD ["python3", "./src/main.py"]
