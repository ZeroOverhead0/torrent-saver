FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TORRENTSAVER_DB=/data/torrents.db \
    ROOT_PATH=""

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Persist the SQLite DB outside the image.
VOLUME ["/data"]
RUN mkdir -p /data

EXPOSE 8080

# Standalone deploy: served at the root (no --root-path). Behind a reverse proxy
# that strips a prefix, set ROOT_PATH and add --root-path in your own command.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8080 --root-path \"$ROOT_PATH\""]
