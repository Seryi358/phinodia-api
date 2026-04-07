# PhinodIA API

Backend API for PhinodIA content generation platform. Generates UGC videos, product images, and landing pages using AI.

## Tech Stack

- **Framework:** FastAPI + uvicorn
- **AI Video:** VEO 3.1 via KIE AI (8s-30s with extensions)
- **AI Images:** Nano Banana Pro via KIE AI
- **AI Scripts:** GPT-4o (AIDA + neuromarketing prompts)
- **Database:** SQLite via SQLAlchemy (async)
- **Payments:** Wompi (Colombian gateway)
- **Email:** Gmail OAuth2

## Quick Start

```bash
cp .env.example .env
# Fill in your API keys in .env

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --reload
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /api/v1/generate/video | Generate UGC video |
| POST | /api/v1/generate/image | Generate product image |
| POST | /api/v1/generate/landing | Generate landing page |
| GET | /api/v1/jobs/status/{id} | Check generation status |
| GET | /api/v1/credits/check | Check credit balance |
| POST | /api/v1/payments/webhook | Wompi payment webhook |
| POST | /api/v1/email/send | Send delivery email |
| GET | /health | Health check |

## Docker

```bash
docker compose up --build
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```
