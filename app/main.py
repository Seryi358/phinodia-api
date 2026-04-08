import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.config import Settings
settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs("data/uploads", exist_ok=True)
    yield


app = FastAPI(title="PhinodIA API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.routers import generate, jobs, credits, payments, email, upload  # noqa: E402

app.include_router(generate.router, prefix="/api/v1/generate", tags=["generate"])
app.include_router(jobs.router, prefix="/api/v1/jobs", tags=["jobs"])
app.include_router(credits.router, prefix="/api/v1/credits", tags=["credits"])
app.include_router(payments.router, prefix="/api/v1/payments", tags=["payments"])
app.include_router(email.router, prefix="/api/v1/email", tags=["email"])
app.include_router(upload.router, prefix="/api/v1/upload", tags=["upload"])


@app.get("/health")
async def health():
    return {"status": "ok"}


# Serve uploaded files
app.mount("/uploads", StaticFiles(directory="data/uploads"), name="uploads")

# Serve frontend static assets (CSS, JS)
app.mount("/static", StaticFiles(directory="frontend/static"), name="static")

# Serve frontend pages (LAST — catch-all for HTML pages)
app.mount("/", StaticFiles(directory="frontend/pages", html=True), name="pages")
