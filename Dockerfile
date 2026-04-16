FROM python:3.12-slim

WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY frontend/ frontend/

# Create data directories
RUN mkdir -p data/uploads

EXPOSE 8000

# 4 workers handle concurrent requests in parallel — single worker was
# serializing /credits/check under load (p95 3s with 50 concurrent calls).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
