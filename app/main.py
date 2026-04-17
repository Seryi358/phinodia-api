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
    # Close shared httpx pool on shutdown
    from app.database import db
    try:
        await db.aclose()
    except Exception:
        pass


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
# We require Content-Length only on the upload endpoint (where streaming a
# multi-GB body past the cap is the actual risk). Webhooks and small JSON
# endpoints can come through proxies that re-emit chunked transfer; the body
# is still bounded by JSON parser limits and the per-route logic.
@app.middleware("http")
async def limit_request_body_size(request: Request, call_next):
    path = request.url.path
    is_upload = path == "/api/v1/upload/image"
    method = request.method.upper()
    cl = request.headers.get("content-length")
    if is_upload and method == "POST":
        if cl is None or not cl.isdigit():
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Content-Length required"}, status_code=411)
        if int(cl) > 11 * 1024 * 1024:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Request body too large"}, status_code=413)
    elif cl and cl.isdigit() and int(cl) > 100 * 1024:
        # Soft cap on non-upload bodies when CL is present.
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Request body too large"}, status_code=413)
    return await call_next(request)


# Per-IP rate limit on email-lookup endpoints to slow down PII enumeration.
# These endpoints intentionally have no auth (UX) but the data shouldn't be
# scrapeable wholesale. 30 req/min/IP per endpoint is plenty for normal use.
import time as _time
from collections import defaultdict, deque
_rate_buckets: dict[tuple[str, str], deque] = defaultdict(deque)
_RATE_LIMITED_PATHS = (
    "/api/v1/credits/check",
    "/api/v1/jobs/by-email",
    "/api/v1/jobs/status",
    "/api/v1/referrals/code",
    "/api/v1/referrals/stats",
    "/api/v1/referrals/register",
    "/api/v1/upload/image",
    "/api/v1/payments/checkout",
)
_RATE_LIMIT = 30   # requests
_RATE_WINDOW = 60  # seconds


@app.middleware("http")
async def rate_limit_email_lookups(request: Request, call_next):
    path = request.url.path
    if any(path.startswith(p) for p in _RATE_LIMITED_PATHS):
        # Prefer the connecting peer over the (spoofable) XFF header. The
        # rate limit is best-effort — a real attacker behind a botnet will
        # rotate IPs anyway, but at least one machine spamming with rotating
        # X-Forwarded-For values can no longer pretend to be 4M distinct hops
        # and exhaust both the limit and our memory.
        client_ip = (request.client.host if request.client else "unknown")
        key = (client_ip, path)
        bucket = _rate_buckets[key]
        now = _time.monotonic()
        # drop entries older than window
        while bucket and now - bucket[0] > _RATE_WINDOW:
            bucket.popleft()
        if len(bucket) >= _RATE_LIMIT:
            from fastapi.responses import JSONResponse
            retry_in = int(_RATE_WINDOW - (now - bucket[0])) + 1
            return JSONResponse(
                {"detail": f"Demasiadas solicitudes. Intenta en {retry_in}s."},
                status_code=429,
                headers={"Retry-After": str(retry_in)},
            )
        bucket.append(now)
        # Free buckets that emptied during cleanup so the dict can't grow
        # unbounded over time (e.g. one IP per request from a NAT).
        if not bucket:
            _rate_buckets.pop(key, None)
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
        # Cache real images, but never cache 404s — uploads use predictable
        # uuid filenames and a CDN/proxy negative-caching a 404 BEFORE the
        # upload finishes would serve stale "not found" to legitimate users.
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=3600"
        else:
            response.headers["Cache-Control"] = "no-store"
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
    # Lock down JSON / upload routes so a future bug that injects HTML in a
    # 4xx body can't fetch attacker scripts. HTML pages keep the wider policy
    # the inline scripts/styles need.
    if path.startswith("/api/") or path.startswith("/uploads/"):
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"

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


# Catch-all for any /api/* GET that the routers didn't match -> proper 405.
# Without this the frontend StaticFiles mount swallows it as 404, which is
# semantically wrong (resource exists, method is wrong).
@app.api_route("/api/{rest_of_path:path}", methods=["GET", "HEAD", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def _api_method_not_allowed(rest_of_path: str):
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": "Method Not Allowed"}, status_code=405)


# Serve frontend pages (LAST — catch-all for HTML pages)
app.mount("/", StaticFiles(directory="frontend/pages", html=True), name="pages")
