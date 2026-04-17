FROM python:3.12-slim

WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY frontend/ frontend/

# Create data directories AND a non-root user so any future RCE in the upload/
# image pipeline doesn't run as root inside the container.
RUN mkdir -p data/uploads \
    && useradd --uid 1000 --create-home --shell /usr/sbin/nologin app \
    && chown -R app:app /app
USER app

EXPOSE 8000

# 4 workers handle concurrent requests in parallel — single worker was
# serializing /credits/check under load (p95 3s with 50 concurrent calls).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
