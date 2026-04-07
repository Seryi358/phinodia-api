import hashlib

PACKAGES_BY_SKU = {
    # Videos 8s (Reel Express)
    "video_8s_5":   {"credits": 5,  "service": "video_8s",  "amount": 2499000},
    "video_8s_10":  {"credits": 10, "service": "video_8s",  "amount": 3499000},
    "video_8s_20":  {"credits": 20, "service": "video_8s",  "amount": 5999000},
    "video_8s_50":  {"credits": 50, "service": "video_8s",  "amount": 12999000},
    # Videos 15s (Reel Standard)
    "video_15s_5":  {"credits": 5,  "service": "video_15s", "amount": 3999000},
    "video_15s_10": {"credits": 10, "service": "video_15s", "amount": 5499000},
    "video_15s_20": {"credits": 20, "service": "video_15s", "amount": 9999000},
    "video_15s_50": {"credits": 50, "service": "video_15s", "amount": 21999000},
    # Videos 22s (Reel Plus)
    "video_22s_5":  {"credits": 5,  "service": "video_22s", "amount": 5999000},
    "video_22s_10": {"credits": 10, "service": "video_22s", "amount": 8499000},
    "video_22s_20": {"credits": 20, "service": "video_22s", "amount": 14999000},
    "video_22s_50": {"credits": 50, "service": "video_22s", "amount": 32999000},
    # Videos 30s (Comercial)
    "video_30s_5":  {"credits": 5,  "service": "video_30s", "amount": 7999000},
    "video_30s_10": {"credits": 10, "service": "video_30s", "amount": 10999000},
    "video_30s_20": {"credits": 20, "service": "video_30s", "amount": 19999000},
    "video_30s_50": {"credits": 50, "service": "video_30s", "amount": 44999000},
    # Images (unchanged)
    "image_5":      {"credits": 5,  "service": "image",        "amount": 1999000},
    "image_10":     {"credits": 10, "service": "image",        "amount": 2499000},
    "image_20":     {"credits": 20, "service": "image",        "amount": 4999000},
    "image_50":     {"credits": 50, "service": "image",        "amount": 11999000},
    # Landing Pages (unchanged)
    "landing_5":    {"credits": 5,  "service": "landing_page", "amount": 7999000},
    "landing_10":   {"credits": 10, "service": "landing_page", "amount": 15999000},
    "landing_20":   {"credits": 20, "service": "landing_page", "amount": 19999000},
    "landing_50":   {"credits": 50, "service": "landing_page", "amount": 29999000},
}


_SERVICE_PRIORITY = ["video_8s", "video_15s", "video_22s", "video_30s", "image", "landing_page"]


def resolve_package(amount_cents: int, sku: str | None = None) -> dict | None:
    if sku and sku in PACKAGES_BY_SKU:
        return PACKAGES_BY_SKU[sku]
    matches = [p for p in PACKAGES_BY_SKU.values() if p["amount"] == amount_cents]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        for service in _SERVICE_PRIORITY:
            for pkg in matches:
                if pkg["service"] == service:
                    return pkg
    return None


def _resolve_property(data: dict, path: str):
    parts = path.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def verify_webhook_signature(event: dict, events_secret: str) -> bool:
    signature = event.get("signature", {})
    properties = signature.get("properties", [])
    expected_checksum = signature.get("checksum", "")
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
    return computed == expected_checksum
