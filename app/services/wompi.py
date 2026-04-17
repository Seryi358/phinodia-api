import hashlib
import hmac

PACKAGES_BY_SKU = {
    # Videos 8s (Reel Express) — 3 planes
    "video_8s_3":   {"credits": 3,  "service": "video_8s",  "amount": 6299000},
    "video_8s_10":  {"credits": 10, "service": "video_8s",  "amount": 18999000},
    "video_8s_25":  {"credits": 25, "service": "video_8s",  "amount": 39999000},
    # Videos 15s (Reel Standard)
    "video_15s_3":  {"credits": 3,  "service": "video_15s", "amount": 11699000},
    "video_15s_10": {"credits": 10, "service": "video_15s", "amount": 33999000},
    "video_15s_25": {"credits": 25, "service": "video_15s", "amount": 72499000},
    # Videos 22s (Reel Plus)
    "video_22s_3":  {"credits": 3,  "service": "video_22s", "amount": 16799000},
    "video_22s_10": {"credits": 10, "service": "video_22s", "amount": 48999000},
    "video_22s_25": {"credits": 25, "service": "video_22s", "amount": 104999000},
    # Videos 30s (Comercial)
    "video_30s_3":  {"credits": 3,  "service": "video_30s", "amount": 21899000},
    "video_30s_10": {"credits": 10, "service": "video_30s", "amount": 64999000},
    "video_30s_25": {"credits": 25, "service": "video_30s", "amount": 137499000},
    # Imagenes — bulk discount: $3997/img → $3499/img (-12%) → $2999/img (-25%)
    "image_3":      {"credits": 3,  "service": "image",        "amount": 1199000},
    "image_10":     {"credits": 10, "service": "image",        "amount": 3499000},
    "image_25":     {"credits": 25, "service": "image",        "amount": 7499000},
    # Landing Pages — bulk discount: $14997 → $12999 (-13%) → $11200 (-25%)
    "landing_3":    {"credits": 3,  "service": "landing_page", "amount": 4499000},
    "landing_10":   {"credits": 10, "service": "landing_page", "amount": 12999000},
    "landing_25":   {"credits": 25, "service": "landing_page", "amount": 27999000},
}


_SERVICE_PRIORITY = ["video_8s", "video_15s", "video_22s", "video_30s", "image", "landing_page"]


def resolve_package(amount_cents: int, sku: str | None = None) -> dict | None:
    """Resolve package strictly: SKU must be known AND amount must match.

    Previous fallbacks (amount-only, or trusting SKU without checking amount)
    let forged webhooks (with leaked events_secret) pick arbitrary credits for
    tiny payments. We always issue references in PH-{sku}-{ts}-{hex} format and
    the webhook router rejects malformed references, so this strict path is the
    only one needed.
    """
    if not sku or sku not in PACKAGES_BY_SKU:
        return None
    pkg = PACKAGES_BY_SKU[sku]
    if pkg["amount"] != amount_cents:
        return None
    return pkg


def _resolve_property(data: dict, path: str):
    parts = path.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


# Signature MUST cover at least these fields, otherwise an attacker (with leaked
# secret OR an empty `properties` list event) could forge events with arbitrary
# transaction details. Wompi only signs id/status/amount_in_cents by default —
# requiring more would 403 every legit webhook. Defense against a leaked secret
# redirecting credits via tampered customer_email/reference belongs at a
# different layer (e.g. server-to-server REST refetch via private_key).
_REQUIRED_SIGNED_PROPS = frozenset({
    "transaction.id",
    "transaction.status",
    "transaction.amount_in_cents",
})


def verify_webhook_signature(event: dict, events_secret: str) -> bool:
    # `or {}` not default-arg — explicit null in the payload would otherwise
    # leak through and AttributeError on .get(). Same pattern for `data`.
    signature = event.get("signature") or {}
    properties = signature.get("properties") or []
    expected_checksum = signature.get("checksum") or ""
    if not isinstance(properties, list) or not _REQUIRED_SIGNED_PROPS.issubset(set(properties)):
        return False
    if not isinstance(expected_checksum, str) or not expected_checksum:
        return False
    values = []
    for prop in properties:
        val = _resolve_property(event.get("data") or {}, prop)
        if val is not None:
            values.append(str(val))
    timestamp = event.get("timestamp", "")
    values.append(str(timestamp))
    values.append(events_secret)
    concat = "".join(values)
    computed = hashlib.sha256(concat.encode()).hexdigest()
    # Constant-time compare avoids signature-byte timing leak.
    return hmac.compare_digest(computed, expected_checksum)
