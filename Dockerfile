FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    DB_PATH=/data/buildconnect.db

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root user; /data holds the SQLite file (mount a volume/disk here for persistence)
RUN useradd -m appuser && mkdir -p /data && chown -R appuser /app /data
USER appuser

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/healthz')"

# Gunicorn serves directly (no Nginx). Single worker + threads keeps SQLite simple.
CMD ["sh", "-c", "exec gunicorn -w 1 --threads 4 -b 0.0.0.0:${PORT} app:app"]
