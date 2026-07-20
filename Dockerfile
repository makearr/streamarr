FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg gosu tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 streamarr \
    && useradd -u 1000 -g streamarr -d /app -M -s /usr/sbin/nologin streamarr

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY streamarr ./streamarr
COPY entrypoint.sh healthcheck.py ./
RUN chmod +x entrypoint.sh && mkdir -p /app/.local && chown -R streamarr:streamarr /app

ENV STREAMARR_CONFIG_DIR=/config \
    STREAMARR_DOWNLOADS_DIR=/downloads \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.local/bin:$PATH" \
    PYTHONPATH="/app/.local/lib/python3.12/site-packages:$PYTHONPATH" \
    HOME=/app

VOLUME ["/config", "/downloads"]
EXPOSE 8585

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ["python", "/app/healthcheck.py"]

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "streamarr.main:app", "--host", "0.0.0.0", "--port", "8585", "--no-access-log"]
