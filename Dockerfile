FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

RUN playwright install --with-deps

ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["uvicorn", "main_api:app", "--host", "0.0.0.0", "--port", "8080"]
