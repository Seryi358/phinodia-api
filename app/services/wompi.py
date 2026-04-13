import hashlib

PACKAGES_BY_SKU = {
    # Videos 8s (Reel Express) - VEO 3.1 Quality: ~$18,000 COP API cost/video
    "video_8s_5":   {"credits": 5,  "service": "video_8s",  "amount": 19999000},    # $199,990 ($40k/vid)
    "video_8s_10":  {"credits": 10, "service": "video_8s",  "amount": 34999000},    # $349,990 ($35k/vid)
    "video_8s_20":  {"credits": 20, "service": "video_8s",  "amount": 59999000},    # $599,990 ($30k/vid)
    "video_8s_50":  {"credits": 50, "service": "video_8s",  "amount": 124999000},   # $1,249,990 ($25k/vid)
    # Videos 15s (Reel Standard) - API cost: ~$36,000 COP/video
    "video_15s_5":  {"credits": 5,  "service": "video_15s", "amount": 35999000},    # $359,990 ($72k/vid)
    "video_15s_10": {"credits": 10, "service": "video_15s", "amount": 61999000},    # $619,990 ($62k/vid)
    "video_15s_20": {"credits": 20, "service": "video_15s", "amount": 99999000},    # $999,990 ($50k/vid)
    "video_15s_50": {"credits": 50, "service": "video_15s", "amount": 189999000},   # $1,899,990 ($38k/vid)
    # Videos 22s (Reel Plus) - API cost: ~$52,000 COP/video
    "video_22s_5":  {"credits": 5,  "service": "video_22s", "amount": 49999000},    # $499,990 ($100k/vid)
    "video_22s_10": {"credits": 10, "service": "video_22s", "amount": 84999000},    # $849,990 ($85k/vid)
    "video_22s_20": {"credits": 20, "service": "video_22s", "amount": 149999000},   # $1,499,990 ($75k/vid)
    "video_22s_50": {"credits": 50, "service": "video_22s", "amount": 299999000},   # $2,999,990 ($60k/vid)
    # Videos 30s (Comercial) - API cost: ~$68,000 COP/video
    "video_30s_5":  {"credits": 5,  "service": "video_30s", "amount": 69999000},    # $699,990 ($140k/vid)
    "video_30s_10": {"credits": 10, "service": "video_30s", "amount": 119999000},   # $1,199,990 ($120k/vid)
    "video_30s_20": {"credits": 20, "service": "video_30s", "amount": 199999000},   # $1,999,990 ($100k/vid)
    "video_30s_50": {"credits": 50, "service": "video_30s", "amount": 399999000},   # $3,999,990 ($80k/vid)
    # Images (Nano Banana 2 - API cost: ~$250 COP/image)
    "image_5":      {"credits": 5,  "service": "image",        "amount": 1999000},  # $19,990
    "image_10":     {"credits": 10, "service": "image",        "amount": 3499000},  # $34,990
    "image_20":     {"credits": 20, "service": "image",        "amount": 5999000},  # $59,990
    "image_50":     {"credits": 50, "service": "image",        "amount": 12999000}, # $129,990
    # Landing Pages (GPT-4o - API cost: ~$1,500 COP/page)
    "landing_5":    {"credits": 5,  "service": "landing_page", "amount": 7999000},  # $79,990
    "landing_10":   {"credits": 10, "service": "landing_page", "amount": 14999000}, # $149,990
    "landing_20":   {"credits": 20, "service": "landing_page", "amount": 24999000}, # $249,990
    "landing_50":   {"credits": 50, "service": "landing_page", "amount": 47999000}, # $479,990
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
