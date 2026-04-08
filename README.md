# PhinodIA API

Plataforma de generacion de contenido de marketing con IA para productos colombianos. Backend + frontend servidos desde FastAPI.

## Servicios

- **Videos UGC** — Videos de producto estilo UGC con acento colombiano (VEO 3.1 + Nano Banana 2 first frame)
- **Imagenes de Producto** — Fotos profesionales estilo Instagram (Nano Banana 2)
- **Landing Pages** — Paginas de venta con framework AIDA y neuromarketing (GPT-4o)

## Stack

| Componente | Tecnologia |
|-----------|-----------|
| Backend | FastAPI + Python 3.12 |
| AI Video | VEO 3.1 via KIE AI |
| AI Images | Nano Banana 2 via KIE AI |
| AI Scripts | GPT-4o (OpenAI) |
| Pagos | Wompi (Colombia) |
| Email | Gmail OAuth2 |
| Database | SQLite + aiosqlite |
| Frontend | HTML/CSS/JS (served by FastAPI) |

## Pipeline de Video

1. Usuario sube imagen de producto + descripcion
2. **Product Analyzer** (GPT-4o) — analisis detallado del producto
3. **Buyer Persona** (GPT-4o) — perfil del creador UGC ideal
4. **First Frame** (Nano Banana 2) — genera primer frame del video
5. **Video Script** (GPT-4o) — guion AIDA con acento colombiano
6. **Video Generation** (VEO 3.1) — genera video base 8s
7. **Extensions** (VEO 3.1) — extiende a 15s/22s/30s si aplica

## Quick Start

```bash
cp .env.example .env
# Fill in your API keys in .env

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --reload
```

## Deploy (EasyPanel / Docker)

```bash
cp .env.example .env
# Editar .env con tus API keys reales
docker compose up -d
```

El servicio expone el puerto 8000 con healthcheck en `/health`.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /api/v1/generate/video | Generate UGC video |
| POST | /api/v1/generate/image | Generate product image |
| POST | /api/v1/generate/landing | Generate landing page |
| GET | /api/v1/jobs/status/{id} | Check generation status |
| GET | /api/v1/credits/check | Check credit balance |
| POST | /api/v1/payments/checkout | Create Wompi checkout |
| POST | /api/v1/payments/webhook | Wompi payment webhook |
| POST | /api/v1/upload/image | Upload product image |
| POST | /api/v1/email/send | Send delivery email |
| GET | /health | Health check |

## Frontend Pages

| Path | Description |
|------|-------------|
| `/` | Home |
| `/videos` | Video generator |
| `/imagenes` | Image generator |
| `/landing-pages` | Landing page generator |
| `/precios` | Pricing + Wompi checkout |
| `/creditos` | Credit balance |
| `/estado` | Job status tracker |

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```
