import hashlib
import hmac

import httpx

# Unified credit packs — ONE wallet pays for everything (video/image/landing).
# amount is COP cents (Wompi convention). Prices = the "minimum / cheapest
# profitable" tier the owner chose (1 credit ~= $2,490 COP base, bulk-discounted
# down to ~$2,200/cr). Per-action credit COSTS live in app/services/credits.py
# (video 3 credits/10s, image 1, landing 6) — NOT here.
PACKAGES_BY_SKU = {
    "credits_6":  {"credits": 6,  "amount": 1690000},   # Prueba  — $16.900
    "credits_20": {"credits": 20, "amount": 5490000},   # Pro     — $54.900 (Más popular)
    "credits_50": {"credits": 50, "amount": 11990000},  # Studio  — $119.900
}


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


async def fetch_transaction(tx_id: str, base_url: str, private_key: str) -> dict | None:
    """Authoritative server-to-server refetch of a Wompi transaction.

    The webhook payload only SIGNS id/status/amount_in_cents; customer_email and
    reference are unsigned, so a leaked events_secret would let an attacker forge
    a webhook that redirects credits to an arbitrary email. We re-read the
    transaction from Wompi's own API (authenticated with the private key) and
    trust ONLY that response for the money-bearing fields. Returns the `data`
    dict, or None on any failure so the caller forces a Wompi retry (503)
    instead of granting on unverified data.
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0)) as client:
            r = await client.get(
                f"{base_url}/transactions/{tx_id}",
                headers={"Authorization": f"Bearer {private_key}"},
            )
        r.raise_for_status()
        return (r.json() or {}).get("data") or {}
    except (httpx.HTTPError, ValueError):
        return None
