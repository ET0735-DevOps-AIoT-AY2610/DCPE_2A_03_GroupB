FROM arm32v7/python:3.8.17-slim-buster

WORKDIR /aiot-fire-app

COPY requirements.txt .

RUN pip install -r requirements.txt

COPY ./src ./src

CMD ["python", "./src/main.py"]
