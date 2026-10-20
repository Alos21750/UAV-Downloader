# UAV Downloader CLI + opt-in authenticated NAS Web queue.
# Pass URL(s); videos are downloaded to /downloads without a GUI.
FROM python:3.12-slim

# ffmpeg = TS->MP4 remux; ca-certificates = TLS. Slim + cleaned apt lists.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# deps first for layer caching
COPY requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY . .

ENV DOWNLOAD_DIR=/downloads \
    UAV_WEB_STATE_DIR=/state \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1
VOLUME ["/downloads"]
EXPOSE 8765

# URL(s) are passed as CMD args (or via URLS / URLS_FILE env)
ENTRYPOINT ["python", "-u", "-m", "uav_downloader.cli.headless"]
