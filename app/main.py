from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import Settings
from app.database import init_db

settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="PhinodIA API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.routers import generate, jobs, credits, payments, email  # noqa: E402

app.include_router(generate.router, prefix="/api/v1/generate", tags=["generate"])
app.include_router(jobs.router, prefix="/api/v1/jobs", tags=["jobs"])
app.include_router(credits.router, prefix="/api/v1/credits", tags=["credits"])
app.include_router(payments.router, prefix="/api/v1/payments", tags=["payments"])
app.include_router(email.router, prefix="/api/v1/email", tags=["email"])


@app.get("/health")
async def health():
    return {"status": "ok"}
