"""Planes del servicio GESTIONADO de PhinodIA (renta mensual auto-cobrada vía Wompi).

Fuente de verdad de los planes, igual que PACKAGES_BY_SKU para los packs de
pago único (services/wompi.py). amount_in_cents = COP cents (convención Wompi).

MODELO DE NEGOCIO (decidido 2026-07-28) — lee esto antes de tocar un precio.
El cliente NO entra a la app ni toca ninguna herramienta: paga un mensual y
recibe los videos terminados. Por eso `credits_per_period` dejó de ser una
unidad de venta y pasó a ser el PRESUPUESTO INTERNO DE PRODUCCIÓN de esa cuenta;
lo que se le promete al cliente es `videos` y `piezas`. Los créditos siguen
existiendo porque el motor de acreditación ya los usa, no porque el cliente los
vea. Consumo interno: 1 video de 10s = 3 créditos (ver media_probe.VIDEO_DURATIONS).

POR QUÉ SE ABANDONÓ EL AUTOSERVICIO: contribución medida de 20.236 COP por
cliente con retención 0% (46 de 46 clientes, dic-2025 a abr-2026) contra un CAC
en Meta Colombia de 20.000-60.000. El LTV era menor o igual que el CAC, así que
el autoservicio no admitía pauta pagada. Además la activación era del 11%: el
81% de los créditos vendidos nunca se usó, porque se le vendía una herramienta a
un segmento con madurez digital de 19,94/100 (CCCE sobre EMICRON 2024 del DANE).
Operando nosotros la herramienta, la activación es del 100% por definición.

ANCLAJE (mercado colombiano verificado 2026-07-28):
  - Productora (Labweb): 850.000 COP por UN reel.
  - Creador UGC principiante: 90.000-200.000 por video.
  - UGC Colombia, plan más barato: 1.590.000/mes por 6 videos = 265.000/video.
'motor' a 890.000 por 24 piezas sale a 111.250 por video: 58% más barato que el
competidor directo y por encima del piso de un creador principiante, que es
donde hay que estar. Bajar de ahí no comunica ganga, comunica producto malo.

ESTRUCTURA (Huber, Payne y Puto 1982 — alternativa asimétricamente dominada):
'semilla_plus' es el SEÑUELO y no está hecho para venderse: cuesta solo 40.000
menos que 'motor' y trae la mitad de los videos, lo que hace que 'motor' se lea
como la elección obvia. 'escala' es el ancla alta. NO hay plan gratis: en un
servicio gestionado el trabajo lo hacemos nosotros, así que un tier gratuito
regala horas. Su lugar lo ocupa el diagnóstico pagado (DIAGNOSTICO_*), que
además filtra por caja e intención antes de invertir tiempo.
"""

MONTHLY = "monthly"
ANNUAL = "annual"

# Anual: se cobra 10 meses y se conceden 12 (2 meses gratis). El descuento va al
# precio; los créditos por período no cambian (se renuevan cada mes del año).
ANNUAL_MONTHS_CHARGED = 10

# Créditos internos que consume una pieza de video de 10s. Debe seguir a
# app/services/media_probe.py: si allí cambia el catálogo, esto cambia.
CREDITS_PER_PIECE = 3

# Diagnóstico de Ganchos: entrada pagada, NO es una suscripción.
# Filtra por caja e intención, genera caja antes de producir, y el video que
# entrega queda como pieza de portafolio del nicho. Se acredita íntegro al
# primer mes si el cliente contrata un plan.
DIAGNOSTICO_AMOUNT_IN_CENTS = 19_000_000   # $190.000
DIAGNOSTICO_PIECES = 1                     # 1 video terminado + 3 ganchos escritos

PLANS_BY_CODE = {
    "semilla": {
        "name": "Semilla",
        "videos": 4,
        "variants_per_video": 0,
        "pieces": 4,
        "credits_per_period": 4 * CREDITS_PER_PIECE,          # 12
        "amount_in_cents": 59_000_000,                          # $590.000 — 147.500/video
        "revisions": 1,
        "delivery_days": 7,
        "watermark": False,
        "popular": False,
    },
    "semilla_plus": {
        # SEÑUELO. Dominado por 'motor': +40.000 da el doble de videos.
        # No se promociona ni se recomienda; existe para que 'motor' gane.
        "name": "Semilla Plus",
        "videos": 4,
        "variants_per_video": 2,
        "pieces": 12,
        "credits_per_period": 12 * CREDITS_PER_PIECE,          # 36
        "amount_in_cents": 85_000_000,                          # $850.000 — 212.500/video
        "revisions": 1,
        "delivery_days": 7,
        "watermark": False,
        "popular": False,
    },
    "motor": {
        "name": "Motor",
        "videos": 8,
        "variants_per_video": 2,
        "pieces": 24,
        "credits_per_period": 24 * CREDITS_PER_PIECE,          # 72
        "amount_in_cents": 89_000_000,                          # $890.000 — 111.250/video
        "revisions": 2,
        "delivery_days": 5,
        "watermark": False,
        "popular": True,                                       # objetivo: 70% de los cierres
    },
    "escala": {
        "name": "Escala",
        "videos": 16,
        "variants_per_video": 3,
        "pieces": 64,
        "credits_per_period": 64 * CREDITS_PER_PIECE,          # 192
        "amount_in_cents": 169_000_000,                         # $1.690.000 — 105.625/video
        "revisions": 0,                                        # 0 = ilimitadas (ver unlimited_revisions)
        "unlimited_revisions": True,
        "delivery_days": 5,
        "watermark": False,
        "popular": False,                                      # ancla alta
    },
}


def resolve_plan(plan_code: str) -> dict | None:
    """Devuelve el plan por código, o None si no existe (nunca inventa uno)."""
    return PLANS_BY_CODE.get(plan_code)


def price_per_video_in_cents(plan_code: str) -> int | None:
    """Precio por video del plan, en centavos. Se muestra en cada columna de la
    tabla de precios: NN/g encontró que un rango de coste con partidas visibles
    convierte mejor que un formulario de cotización. Es también lo que hace
    evidente que 'semilla_plus' está dominado por 'motor'."""
    plan = PLANS_BY_CODE.get(plan_code)
    if not plan or plan["videos"] <= 0:
        return None
    return plan["amount_in_cents"] // plan["videos"]


def period_amount_in_cents(plan_code: str, billing_interval: str = MONTHLY) -> int | None:
    """Monto a cobrar en un período según el intervalo.

    monthly -> precio mensual. annual -> 10× el mensual (2 meses gratis),
    cobrado de una vez al inicio del ciclo. None si el plan no existe o no tiene
    precio (ningún plan gestionado es gratuito, pero la guarda se mantiene).
    """
    plan = PLANS_BY_CODE.get(plan_code)
    if not plan or plan["amount_in_cents"] <= 0:
        return None
    if billing_interval == ANNUAL:
        return plan["amount_in_cents"] * ANNUAL_MONTHS_CHARGED
    return plan["amount_in_cents"]
