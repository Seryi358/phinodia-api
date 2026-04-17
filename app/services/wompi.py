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
    # Imagenes
    "image_3":      {"credits": 3,  "service": "image",        "amount": 1199000},
    "image_10":     {"credits": 10, "service": "image",        "amount": 3999000},
    "image_25":     {"credits": 25, "service": "image",        "amount": 9999000},
    # Landing Pages
    "landing_3":    {"credits": 3,  "service": "landing_page", "amount": 4499000},
    "landing_10":   {"credits": 10, "service": "landing_page", "amount": 14999000},
    "landing_25":   {"credits": 25, "service": "landing_page", "amount": 37499000},
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
# transaction details. Including customer_email + reference + currency means
# even with a leaked secret the attacker can't redirect credits to their own
# email or alter the SKU/amount we resolve from the reference string.
_REQUIRED_SIGNED_PROPS = frozenset({
    "transaction.id",
    "transaction.status",
    "transaction.amount_in_cents",
    "transaction.customer_email",
    "transaction.reference",
    "transaction.currency",
})


def verify_webhook_signature(event: dict, events_secret: str) -> bool:
    signature = event.get("signature", {})
    properties = signature.get("properties", [])
    expected_checksum = signature.get("checksum", "")
    if not isinstance(properties, list) or not _REQUIRED_SIGNED_PROPS.issubset(set(properties)):
        return False
    if not isinstance(expected_checksum, str) or not expected_checksum:
        return False
    values = []
    for prop in properties:
        val = _resolve_property(event.get("data", {}), prop)
        if val is not None:
            values.append(str(val))
    timestamp = event.get("timestamp", "")
    values.append(str(timestamp))
    values.append(events_secret)
    concat = "".join(values)
    computed = hashlib.sha256(concat.encode()).hexdigest()
    # Constant-time compare avoids signature-byte timing leak.
    return hmac.compare_digest(computed, expected_checksum)
