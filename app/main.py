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


# Reject oversized request bodies before parsing — prevents DoS via huge JSON
# payloads. Upload route accepts up to 10MB (multipart); JSON bodies are tiny.
@app.middleware("http")
async def limit_request_body_size(request: Request, call_next):
    path = request.url.path
    is_upload = path == "/api/v1/upload/image"
    max_bytes = 11 * 1024 * 1024 if is_upload else 100 * 1024
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > max_bytes:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Request body too large"}, status_code=413)
    return await call_next(request)


@app.middleware("http")
async def add_cache_and_security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    path = request.url.path

    # Cache control
    if path.startswith("/static/css/") or path.startswith("/static/js/"):
        response.headers["Cache-Control"] = "public, max-age=3600"
    elif path.startswith("/static/images/"):
        response.headers["Cache-Control"] = "public, max-age=86400"
    elif path.startswith("/uploads/"):
        response.headers["Cache-Control"] = "public, max-age=3600"
    elif path.endswith(".html") or path.endswith("/") or path == "/":
        response.headers["Cache-Control"] = "no-cache"

    # Security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # SAMEORIGIN allows the landing page iframe preview on /estado/ and /mis-generaciones/
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    # Permissions-Policy: disable APIs we don't use to limit fingerprinting
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

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


@app.api_route("/robots.txt", methods=["GET", "HEAD"])
async def robots():
    from fastapi.responses import PlainTextResponse
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /uploads/\n"
        "Disallow: /creditos\n"
        "Disallow: /estado\n"
        "Disallow: /mis-generaciones\n"
        "Sitemap: https://app.phinodia.com/sitemap.xml\n"
    )
    return PlainTextResponse(body, headers={"Cache-Control": "public, max-age=86400"})


@app.api_route("/sitemap.xml", methods=["GET", "HEAD"])
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
