import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.config import Settings
settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs("data/uploads", exist_ok=True)
    yield


app = FastAPI(
    title="PhinodIA API", version="2.0.0", lifespan=lifespan,
    docs_url=None, redoc_url=None, openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_cache_headers(request: Request, call_next):
    response: Response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/css/") or path.startswith("/static/js/"):
        response.headers["Cache-Control"] = "public, max-age=3600"
    elif path.startswith("/static/images/"):
        response.headers["Cache-Control"] = "public, max-age=86400"
    elif path.startswith("/uploads/"):
        response.headers["Cache-Control"] = "public, max-age=3600"
    elif path.endswith(".html") or path.endswith("/") or path == "/":
        response.headers["Cache-Control"] = "no-cache"
    return response

from app.routers import generate, jobs, credits, payments, upload, referrals  # noqa: E402

app.include_router(generate.router, prefix="/api/v1/generate", tags=["generate"])
app.include_router(jobs.router, prefix="/api/v1/jobs", tags=["jobs"])
app.include_router(credits.router, prefix="/api/v1/credits", tags=["credits"])
app.include_router(payments.router, prefix="/api/v1/payments", tags=["payments"])
app.include_router(upload.router, prefix="/api/v1/upload", tags=["upload"])
app.include_router(referrals.router, prefix="/api/v1/referrals", tags=["referrals"])


@app.get("/health")
async def health():
    return {"status": "ok"}


# Serve favicon.ico at root (browsers request this automatically)
@app.api_route("/favicon.ico", methods=["GET", "HEAD"])
async def favicon():
    from fastapi.responses import FileResponse
    return FileResponse("frontend/static/images/favicon-32.png", media_type="image/png")


@app.get("/robots.txt")
async def robots():
    from fastapi.responses import PlainTextResponse
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /uploads/\n"
        "Disallow: /creditos\n"
        "Disallow: /estado\n"
        "Sitemap: https://app.phinodia.com/sitemap.xml\n"
    )
    return PlainTextResponse(body, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/sitemap.xml")
async def sitemap():
    from fastapi.responses import Response
    pages = ["/", "/videos", "/imagenes", "/landing-pages", "/precios", "/referidos"]
    urls = "\n".join(
        f"  <url><loc>https://app.phinodia.com{p}</loc><changefreq>weekly</changefreq></url>"
        for p in pages
    )
    body = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}\n</urlset>\n'
    return Response(body, media_type="application/xml", headers={"Cache-Control": "public, max-age=86400"})


# Serve uploaded files
app.mount("/uploads", StaticFiles(directory="data/uploads"), name="uploads")

# Serve frontend static assets (CSS, JS)
app.mount("/static", StaticFiles(directory="frontend/static"), name="static")

# Serve frontend pages (LAST — catch-all for HTML pages)
app.mount("/", StaticFiles(directory="frontend/pages", html=True), name="pages")
