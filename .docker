# Dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . /app

RUN apt-get update && apt-get install -y curl \
    && pip install --upgrade pip \
    && pip install -r requirements.txt \
    && playwright install --with-deps

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["uvicorn", "main_api:app", "--host", "0.0.0.0", "--port", "8080"]
