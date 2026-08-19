FROM python:3.8.11-slim-buster

WORKDIR /app

COPY requirements.txt .

RUN pip install -r requirements.txt

COPY src ./src

WORKDIR /app/src

CMD ["python3", "main.py"]
