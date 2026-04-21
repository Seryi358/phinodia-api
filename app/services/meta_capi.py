"""Meta Conversions API (CAPI) — server-side event mirror for the Pixel.

Why this exists in 2026:
- Browser Pixel loses 30-50% of events to Safari ITP, iOS 18 Privacy Relay,
  ad-blockers, and EU/CO consent banners (Habeas Data here in Colombia).
- Advantage+ Sales Campaigns rely on event volume to escape learning phase
  (need ~50 conversions/week per ad set). Without CAPI, a $1M COP/month
  account can't escape learning and CPA stays inflated 2-3x.
- Every event we mirror is sent with the SAME `event_id` the Pixel uses
  (when both fire) so Meta dedupes within its 48h window — never a
  double-count, but maximum coverage.

Event Match Quality (EMQ): Meta scores how well our user data matches
their graph. Score >= 8.0 unlocks the best optimization. We send:
  hashed: email, phone (if available), first/last name parsed from email
  raw: client_ip_address, client_user_agent, fbp cookie, fbc click id
The more we send, the better the algorithm bids for our actual buyers.

Failure mode: ALL CAPI calls are best-effort. If Meta is down or returns
a 4xx, we log + swallow — the user's purchase/credit grant must NEVER
fail just because tracking failed.
"""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# Meta API version — pinned so a server-side breaking change in v22+
# doesn't silently break our event delivery overnight. Bump after testing
# in Meta Events Manager → Test Events.
_META_API_VERSION = "v21.0"


def _sha256_norm(value: str | None) -> str | None:
    """Hash + normalize per Meta CAPI spec.

    Meta requires lowercase, trimmed, then SHA-256 hex. Empty/None returns
    None so we skip the field entirely (sending '' would lower EMQ score
    by suggesting we have data when we don't)."""
    if not value:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def new_event_id() -> str:
    """Generate the dedup key. UUID4 is what Meta recommends; we generate
    server-side so the frontend Pixel call can echo it back via a hidden
    field or query param. If you ever change format, also update the
    Pixel snippet (frontend/static/js/meta-pixel.js)."""
    return uuid.uuid4().hex


def _split_name_from_email(email: str | None) -> tuple[str | None, str | None]:
    """Best-effort first/last-name extraction from email local-part.

    `juan.perez@gmail.com` → ('juan', 'perez'). Boosts EMQ by ~0.3 with
    near-zero cost. We never trust this enough to display, only to hash
    and let Meta match against their graph."""
    if not email or "@" not in email:
        return None, None
    local = email.split("@", 1)[0]
    # Strip + addresses (juan+spam@gmail.com → juan)
    local = local.split("+", 1)[0]
    parts = [p for p in local.replace("_", ".").replace("-", ".").split(".") if p.isalpha()]
    if len(parts) >= 2:
        return parts[0], parts[1]
    if len(parts) == 1:
        return parts[0], None
    return None, None


class MetaCAPI:
    """Best-effort CAPI client. Never raises — every method swallows."""

    def __init__(self):
        s = get_settings()
        self.pixel_id = s.meta_pixel_id
        self.access_token = s.meta_capi_access_token
        self.test_event_code = s.meta_test_event_code
        self._enabled = bool(self.pixel_id and self.access_token)
        if not self._enabled:
            logger.info("Meta CAPI disabled (pixel_id or access_token missing)")

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def send_event(
        self,
        event_name: str,
        event_id: str | None = None,
        *,
        email: str | None = None,
        phone: str | None = None,
        client_ip: str | None = None,
        user_agent: str | None = None,
        fbp: str | None = None,           # _fbp cookie ("fb.1.<ts>.<rand>")
        fbc: str | None = None,           # built from ?fbclid= ("fb.1.<ts>.<fbclid>")
        event_source_url: str | None = None,
        action_source: str = "website",
        value_cop: int | None = None,     # in pesos (NOT cents) — Meta wants major units
        currency: str = "COP",
        content_ids: list[str] | None = None,
        content_type: str = "product",
        custom_data: dict[str, Any] | None = None,
    ) -> bool:
        """Mirror a single event to Meta. Returns True if accepted (200),
        False on any error (logged but swallowed)."""
        if not self._enabled:
            return False

        eid = event_id or new_event_id()
        now = int(time.time())

        first_name, last_name = _split_name_from_email(email)
        user_data: dict[str, Any] = {}
        # Hashed PII (Meta requires SHA-256 hex). Skip None fields entirely
        # — sending null/empty drops EMQ score.
        if (h := _sha256_norm(email)):
            user_data["em"] = [h]
        if (h := _sha256_norm(phone)):
            user_data["ph"] = [h]
        if (h := _sha256_norm(first_name)):
            user_data["fn"] = [h]
        if (h := _sha256_norm(last_name)):
            user_data["ln"] = [h]
        if (h := _sha256_norm("co")):
            user_data["country"] = [h]  # We're CO-only
        # Raw signals (Meta hashes server-side)
        if client_ip:
            user_data["client_ip_address"] = client_ip
        if user_agent:
            user_data["client_user_agent"] = user_agent
        if fbp:
            user_data["fbp"] = fbp
        if fbc:
            user_data["fbc"] = fbc

        event: dict[str, Any] = {
            "event_name": event_name,
            "event_time": now,
            "event_id": eid,
            "action_source": action_source,
            "user_data": user_data,
        }
        if event_source_url:
            event["event_source_url"] = event_source_url

        cd: dict[str, Any] = dict(custom_data or {})
        if value_cop is not None:
            cd["value"] = float(value_cop)
            cd["currency"] = currency
        if content_ids:
            cd["content_ids"] = content_ids
            cd["content_type"] = content_type
        if cd:
            event["custom_data"] = cd

        body: dict[str, Any] = {"data": [event]}
        # Test event code routes to Events Manager → Test Events tab
        # instead of production stream. Set during E2E debugging only.
        if self.test_event_code:
            body["test_event_code"] = self.test_event_code

        url = f"https://graph.facebook.com/{_META_API_VERSION}/{self.pixel_id}/events"
        try:
            # 5s timeout: this fires on the hot path of /payments/webhook
            # which Wompi holds open. Meta CAPI normally returns in <500ms;
            # 5s is generous for a network blip but still well under the
            # 30s Wompi timeout.
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
                r = await client.post(
                    url,
                    params={"access_token": self.access_token},
                    json=body,
                )
            if r.status_code >= 400:
                # Don't log access_token. Body might have hashed PII — log
                # only event_name + status so ops can spot patterns.
                logger.warning(
                    "Meta CAPI %s rejected event %s: HTTP %d body=%s",
                    self.pixel_id, event_name, r.status_code, r.text[:300],
                )
                return False
            return True
        except Exception as e:
            logger.warning("Meta CAPI send failed for %s: %s", event_name, type(e).__name__)
            return False


# Singleton for hot paths
_capi: MetaCAPI | None = None


def get_capi() -> MetaCAPI:
    global _capi
    if _capi is None:
        _capi = MetaCAPI()
    return _capi
