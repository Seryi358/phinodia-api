import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from app.config import Settings
settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs("data/uploads", exist_ok=True)
    yield
    # Drain in-flight generation background tasks before tearing down the
    # Supabase pool so they can checkpoint state (refund credits / mark
    # jobs failed). Without this, a redeploy mid-generation crashes the
    # tasks against a closed httpx client and leaves jobs in "processing"
    # until the 30-min auto-fail sweeper picks them up.
    import asyncio as _asyncio
    import logging as _logging
    _log = _logging.getLogger(__name__)
    try:
        from app.routers.generate import _background_tasks
        if _background_tasks:
            pending = [t for t in _background_tasks if not t.done()]
            if pending:
                _log.info("Draining %d in-flight generation tasks (max 25s)", len(pending))
                done, still_pending = await _asyncio.wait(pending, timeout=25)
                if still_pending:
                    _log.info("Cancelling %d unfinished tasks", len(still_pending))
                    for t in still_pending:
                        t.cancel()
                    # AWAIT the cancellations so each task's except block
                    # gets event-loop time to refund + checkpoint BEFORE we
                    # tear down the httpx pool below. Without this gather
                    # the workers' refund code runs against a closed client
                    # and silently drops the user's credit.
                    await _asyncio.wait(still_pending, timeout=10)
    except Exception as e:
        _log.warning("Background task drain failed: %s", e)
    # Close shared httpx pool on shutdown
    from app.database import db
    try:
        await db.aclose()
    except Exception as e:
        _log.warning("db.aclose() failed: %s", e)


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


# Spanish-localize FastAPI's 422 validation errors so toast messages don't
# show "Field required" / "Value error, ..." in English to es-CO users.
_FIELD_LABELS = {
    "image_url": "imagen",
    "product_name": "nombre del producto",
    "description": "descripcion",
    "email": "correo electronico",
    "duration": "duracion",
    "format": "formato",
    "aspect_ratio": "formato",
    "data_consent": "tratamiento de datos",
    "sku": "paquete",
    "referred_email": "correo referido",
    "referral_code": "codigo de referido",
    "file": "archivo",
    "job_id": "ID del trabajo",
}


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    msgs = []
    for err in exc.errors():
        loc = err.get("loc") or []
        field = loc[-1] if loc else ""
        label = _FIELD_LABELS.get(str(field), str(field))
        etype = err.get("type", "")
        msg = err.get("msg", "")
        if etype == "missing":
            msgs.append(f"Falta el campo: {label}")
        elif etype == "value_error" and msg.startswith("Value error, "):
            msgs.append(msg.removeprefix("Value error, "))
        elif "email" in etype:
            msgs.append("Correo electronico no valido")
        elif etype == "uuid_parsing":
            msgs.append("ID no valido")
        elif "literal" in etype:
            msgs.append(f"Valor no permitido en {label}")
        elif "string_too_short" in etype or "min_length" in etype:
            msgs.append(f"{label.capitalize()} no puede estar vacio")
        elif "string_too_long" in etype or "max_length" in etype:
            msgs.append(f"{label.capitalize()} es demasiado largo")
        elif etype in ("json_invalid", "json_type") or "json" in etype:
            msgs.append("Cuerpo invalido: se esperaba JSON valido")
        elif "model_attributes_type" in etype or "dict_type" in etype or "model_type" in etype:
            msgs.append("Cuerpo invalido: se esperaba un objeto JSON")
        else:
            msgs.append(msg or f"Valor no valido en {label}")
    detail = "; ".join(msgs) if msgs else "Datos invalidos"
    return JSONResponse({"detail": detail}, status_code=422)


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


_RATE_BUCKET_MAX = 5000  # if dict exceeds this, sweep stale entries


def _evict_stale_buckets(now: float) -> None:
    """Drop buckets whose newest entry is older than the rate window. Called
    periodically when the dict grows large to bound memory under attack."""
    stale = [k for k, b in _rate_buckets.items() if not b or now - b[-1] > _RATE_WINDOW]
    for k in stale:
        _rate_buckets.pop(k, None)


@app.middleware("http")
async def rate_limit_email_lookups(request: Request, call_next):
    path = request.url.path
    if any(path.startswith(p) for p in _RATE_LIMITED_PATHS):
        # KNOWN LIMITATION: uvicorn --proxy-headers --forwarded-allow-ips '*'
        # makes request.client.host follow XFF, so a single attacker rotating
        # X-Forwarded-For can mint unlimited rate-limit keys. We tolerate this
        # because (a) the limit is a UX speed-bump not a hard security boundary,
        # (b) tightening --forwarded-allow-ips requires knowing EasyPanel's
        # proxy CIDR, and (c) the LRU eviction at _RATE_BUCKET_MAX caps memory
        # under attack so the spoof can't OOM the worker either.
        client_ip = (request.client.host if request.client else "unknown")
        key = (client_ip, path)
        now = _time.monotonic()
        # Periodic sweep of stale buckets to bound dict size under burst
        # traffic / NAT churn — prior `if not bucket: pop` after append was
        # dead code (bucket always non-empty after appending).
        if len(_rate_buckets) > _RATE_BUCKET_MAX:
            _evict_stale_buckets(now)
        bucket = _rate_buckets[key]
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
    return await call_next(request)


@app.middleware("http")
async def add_cache_and_security_headers(request: Request, call_next):
    path = request.url.path
    # Reject dotfiles BEFORE StaticFiles serves them, but build the response
    # ourselves so the security-headers block below still fires.
    if (path.startswith("/uploads/") or path.startswith("/static/")) and \
            any(seg.startswith(".") for seg in path.split("/") if seg):
        response: Response = JSONResponse({"detail": "Not Found"}, status_code=404)
    else:
        response = await call_next(request)

    # Cache control. Apply long max-age ONLY to successful responses so a 404
    # for a typoed asset (or a /uploads/ file requested before upload finishes)
    # doesn't get negative-cached for hours by browsers/CDNs. Use broad
    # /static/ and /uploads/ prefixes so a typo like /static/foo.css (which
    # doesn't match /static/css/) still gets the no-store treatment on 404.
    is_static = path.startswith("/static/") or path.startswith("/uploads/")
    if is_static:
        if response.status_code == 200:
            if path.startswith("/static/images/"):
                response.headers["Cache-Control"] = "public, max-age=86400"
            else:
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
    # CSP on JSON / upload routes — include the bare `/api` path so a 404
    # served from /api itself (no trailing slash) gets the same lockdown.
    if path == "/api" or path.startswith("/api/") or path.startswith("/uploads/"):
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        # Also ensure 4xx/5xx API responses (e.g. 422 from path-param
        # validation) carry no-store so a CDN/proxy can't poison the cache
        # with attacker-shaped error bodies.
        if response.status_code >= 400 and "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"

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
    # Trailing slashes match what FastAPI redirects to, so crawlers don't
    # have to follow a 307 hop on every URL (Google "Page with redirect"
    # warning in Search Console).
    pages = ["/", "/videos/", "/imagenes/", "/landing-pages/", "/precios/", "/referidos/"]
    urls = "\n".join(
        f"  <url><loc>https://app.phinodia.com{p}</loc><changefreq>weekly</changefreq></url>"
        for p in pages
    )
    body = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}\n</urlset>\n'
    return Response(body, media_type="application/xml", headers={"Cache-Control": "public, max-age=86400"})


# Note: dotfile filter moved into add_cache_and_security_headers below so 404
# responses carry the same header envelope as everything else (was returning
# bare 404 without HSTS/CSP/no-store, defeating the point of the middleware).


# Serve uploaded files
app.mount("/uploads", StaticFiles(directory="data/uploads"), name="uploads")

# Serve frontend static assets (CSS, JS)
app.mount("/static", StaticFiles(directory="frontend/static"), name="static")


# Catch-all for /api/* paths the routers didn't match. Returns 405 with the
# accurate Allow header only for paths that DO exist as routes; otherwise 404.
# Map of EXACT route paths (matching FastAPI router registrations) to the
# methods they accept. The 405 fallback only fires for an exact match here;
# anything else returns 404 (don't leak prefix existence + don't waste
# clients' probes on routes that don't exist).
_ROUTE_METHODS = {
    # generate
    "/api/v1/generate/video": "POST",
    "/api/v1/generate/image": "POST",
    "/api/v1/generate/landing": "POST",
    # jobs (status takes a UUID path arg — match by prefix in code below)
    "/api/v1/jobs/by-email": "GET, HEAD",
    # credits
    "/api/v1/credits/check": "GET, HEAD",
    # payments
    "/api/v1/payments/checkout": "POST",
    "/api/v1/payments/webhook": "POST",
    # upload
    "/api/v1/upload/image": "POST",
    # referrals
    "/api/v1/referrals/code": "GET, HEAD",
    "/api/v1/referrals/stats": "GET, HEAD",
    "/api/v1/referrals/register": "POST",
}


@app.api_route("/api/{rest_of_path:path}", methods=["GET", "HEAD", "PUT", "DELETE", "PATCH", "OPTIONS", "POST"])
async def _api_unknown_or_method_not_allowed(rest_of_path: str, request: Request):
    full = "/api/" + rest_of_path
    # /jobs/status/<uuid> is special — only the exact UUID-bearing path is
    # a real route. Treat any /api/v1/jobs/status/<anything> as a 405 if the
    # method is wrong (the actual route would 422 on bad UUID, not 405).
    if full.startswith("/api/v1/jobs/status/"):
        # GET/HEAD with bad UUID hits the real route's 422 validator. Anything
        # else (e.g. POST /jobs/status/<uuid>) is method-not-allowed. Path
        # without a UUID is just unknown → 404.
        if request.method in ("GET", "HEAD"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return JSONResponse({"detail": "Method Not Allowed"}, status_code=405, headers={"Allow": "GET, HEAD"})
    allow = _ROUTE_METHODS.get(full)
    if allow:
        return JSONResponse({"detail": "Method Not Allowed"}, status_code=405, headers={"Allow": allow})
    return JSONResponse({"detail": "Not Found"}, status_code=404)


# Serve frontend pages (LAST — catch-all for HTML pages)
app.mount("/", StaticFiles(directory="frontend/pages", html=True), name="pages")
