FROM python:3.11-slim

WORKDIR /app

COPY src/app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/app/ .

RUN mkdir -p /app/database /app/logs
VOLUME ["/app/database", "/app/logs"]

CMD ["python", "main.py"]
