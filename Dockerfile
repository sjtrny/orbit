FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/sjtrny/orbit" \
    org.opencontainers.image.description="A small, self-hosted disk usage viewer for Linux."

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ORBIT_BIND=0.0.0.0 \
    ORBIT_HOST_ROOT=/host \
    ORBIT_DATA=/data \
    SQLITE_TMPDIR=/data/tmp

RUN mkdir -p /data
COPY app.py ./
COPY static ./static

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"]
STOPSIGNAL SIGTERM
CMD ["python", "app.py"]
