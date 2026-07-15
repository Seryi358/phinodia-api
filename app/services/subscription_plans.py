"""Planes de suscripción de PhinodIA (renta mensual auto-cobrada vía Wompi).

Fuente de verdad de los planes, igual que PACKAGES_BY_SKU para los packs de
pago único (services/wompi.py). amount_in_cents = COP cents (convención Wompi).
credits_per_period = créditos que se acreditan al inicio de cada período pagado.

Estructura de precios (decoy pricing): 'tienda' es el ancla "Más popular";
'escala' es el ancla alta que hace ver a 'tienda' como la opción sensata;
'gratis' es el gancho freemium (output con marca de agua) que llena el embudo.
Los packs de pago único (PACKAGES_BY_SKU) siguen existiendo como top-ups.
"""

MONTHLY = "monthly"
ANNUAL = "annual"

# Anual: se cobra 10 meses y se conceden 12 (2 meses gratis). El descuento va al
# precio; los créditos por período no cambian (se renuevan cada mes del año).
ANNUAL_MONTHS_CHARGED = 10

PLANS_BY_CODE = {
    "gratis": {
        "name": "Gratis",
        "credits_per_period": 12,
        "amount_in_cents": 0,
        "watermark": True,   # el freemium marca el output — gancho de conversión
        "popular": False,
    },
    "emprendedor": {
        "name": "Emprendedor",
        "credits_per_period": 30,
        "amount_in_cents": 4_990_000,   # $49.900
        "watermark": False,
        "popular": False,
    },
    "tienda": {
        "name": "Tienda",
        "credits_per_period": 60,
        "amount_in_cents": 9_990_000,   # $99.900
        "watermark": False,
        "popular": True,                # "Más popular" — ancla de decisión
    },
    "escala": {
        "name": "Escala",
        "credits_per_period": 120,
        "amount_in_cents": 19_990_000,  # $199.900 — ancla alta
        "watermark": False,
        "popular": False,
    },
}


def resolve_plan(plan_code: str) -> dict | None:
    """Devuelve el plan por código, o None si no existe (nunca inventa uno)."""
    return PLANS_BY_CODE.get(plan_code)


def period_amount_in_cents(plan_code: str, billing_interval: str = MONTHLY) -> int | None:
    """Monto a cobrar en un período según el intervalo.

    monthly -> precio mensual. annual -> 10× el mensual (2 meses gratis),
    cobrado de una vez al inicio del ciclo. None si el plan no existe o es el
    gratis (el plan gratis nunca se cobra).
    """
    plan = PLANS_BY_CODE.get(plan_code)
    if not plan or plan["amount_in_cents"] <= 0:
        return None
    if billing_interval == ANNUAL:
        return plan["amount_in_cents"] * ANNUAL_MONTHS_CHARGED
    return plan["amount_in_cents"]
