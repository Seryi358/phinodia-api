"""Pruebas del catálogo del servicio gestionado.

No cubren solo aritmética: blindan las tres decisiones de negocio del 2026-07-28
que un cambio de precio descuidado puede romper en silencio.
  1. 'motor' debe DOMINAR a 'semilla_plus' (efecto señuelo). Si alguien sube
     'motor' o baja 'semilla_plus' hasta invertir la relación, el señuelo deja de
     funcionar y la tabla de precios empieza a empujar al plan equivocado.
  2. Ningún plan puede caer por debajo del piso de un creador UGC principiante
     en Colombia (90.000/video). Por debajo de ahí el precio comunica producto
     malo, no ganga.
  3. Los créditos internos deben alcanzar para producir lo prometido. Si un plan
     promete más piezas de las que su presupuesto de créditos cubre, la cuenta se
     queda sin producción a mitad de mes y el fallo aparece ante el cliente.

Los importes se derivan de PLANS_BY_CODE, nunca se copian: duplicar precios en
los tests dejó la suite en rojo desde el 2026-07-14 (ver tests/test_wompi.py).
"""
import pytest

from app.services.subscription_plans import (
    ANNUAL,
    ANNUAL_MONTHS_CHARGED,
    CREDITS_PER_PIECE,
    DIAGNOSTICO_AMOUNT_IN_CENTS,
    MONTHLY,
    PLANS_BY_CODE,
    period_amount_in_cents,
    price_per_video_in_cents,
    resolve_plan,
)

# Piso de mercado verificado (laboratorioweb.com.co, 2026): un creador UGC
# principiante en Colombia cobra entre 90.000 y 200.000 por video.
PISO_MERCADO_POR_VIDEO_CENTS = 9_000_000   # 90.000 pesos x 100


def _unitario(code: str) -> int:
    """price_per_video_in_cents con el None descartado: para un plan del catálogo
    nunca debe ser None, y si lo es queremos un fallo con mensaje claro y no un
    TypeError a mitad de una comparación."""
    v = price_per_video_in_cents(code)
    assert v is not None, f"price_per_video_in_cents({code!r}) devolvió None"
    return v


def _periodo(code: str, interval: str) -> int:
    v = period_amount_in_cents(code, interval)
    assert v is not None, f"period_amount_in_cents({code!r}, {interval!r}) devolvió None"
    return v


def test_precios_en_pesos_son_los_acordados():
    """Fija los precios en PESOS, no en centavos.

    ESTA PRUEBA EXISTE POR UN FALLO REAL: los importes se escribieron como
    `59_000_00` (5.900.000 centavos = 59.000 pesos) cuando debían ser
    `59_000_000` (590.000 pesos). Se desplegaron a producción 10 veces más
    barancos de lo acordado. Ninguna prueba lo detectó porque el piso de mercado
    estaba escrito con la MISMA escala equivocada, así que validó el error en vez
    de encontrarlo.

    La lección: cuando un valor tiene unidad, hay que anclarlo a la unidad en la
    que se habla del negocio (pesos), no a la de almacenamiento (centavos). Un
    test que comparte la convención del código no comprueba nada.
    """
    esperado_pesos = {
        "semilla": 590_000,
        "semilla_plus": 850_000,
        "motor": 890_000,
        "escala": 1_690_000,
    }
    reales = {c: p["amount_in_cents"] // 100 for c, p in PLANS_BY_CODE.items()}
    assert reales == esperado_pesos
    assert DIAGNOSTICO_AMOUNT_IN_CENTS // 100 == 190_000


def test_motor_encaja_entre_los_anclas_del_mercado():
    """Comprobación de cordura contra precios externos verificados (2026-07-28):
    un creador UGC principiante en Colombia cobra desde 90.000 por video y el
    competidor directo (UGC Colombia) cobra 265.000. 'motor' debe quedar dentro
    de esa horquilla: por debajo del piso comunica producto malo, por encima del
    competidor pierde el argumento de precio."""
    unitario_pesos = _unitario("motor") // 100
    assert 90_000 < unitario_pesos < 265_000, f"{unitario_pesos} COP/video fuera de rango"


def test_resolve_plan_desconocido_no_inventa():
    assert resolve_plan("no_existe") is None
    assert resolve_plan("") is None


@pytest.mark.parametrize("code", list(PLANS_BY_CODE))
def test_todo_plan_gestionado_es_de_pago(code):
    """No hay tier gratuito: en un servicio gestionado el trabajo lo hacemos
    nosotros, así que un plan gratis regala horas."""
    assert PLANS_BY_CODE[code]["amount_in_cents"] > 0
    assert _periodo(code, MONTHLY) > 0


@pytest.mark.parametrize("code", list(PLANS_BY_CODE))
def test_piezas_cuadran_con_videos_y_variantes(code):
    p = PLANS_BY_CODE[code]
    assert p["pieces"] == p["videos"] * (1 + p["variants_per_video"])


@pytest.mark.parametrize("code", list(PLANS_BY_CODE))
def test_creditos_internos_alcanzan_para_lo_prometido(code):
    """El presupuesto de producción debe cubrir todas las piezas prometidas."""
    p = PLANS_BY_CODE[code]
    assert p["credits_per_period"] >= p["pieces"] * CREDITS_PER_PIECE


@pytest.mark.parametrize("code", list(PLANS_BY_CODE))
def test_ningun_plan_baja_del_piso_de_mercado(code):
    assert _unitario(code) >= PISO_MERCADO_POR_VIDEO_CENTS


def test_motor_domina_al_senuelo():
    """El señuelo solo funciona si 'motor' es obviamente mejor: más videos por
    una diferencia de precio pequeña."""
    motor = PLANS_BY_CODE["motor"]
    senuelo = PLANS_BY_CODE["semilla_plus"]

    assert motor["videos"] > senuelo["videos"], "el señuelo no está dominado en volumen"
    assert motor["pieces"] > senuelo["pieces"]
    # La diferencia de precio debe ser marginal frente al salto de valor: el
    # señuelo funciona porque por ~40.000 pesos más te llevas el doble de videos.
    # Umbral en PESOS a propósito: expresarlo en centavos fue lo que ocultó el
    # fallo de escala de los precios (ver test_precios_en_pesos_son_los_acordados).
    delta_pesos = (motor["amount_in_cents"] - senuelo["amount_in_cents"]) // 100
    assert 0 < delta_pesos <= 60_000, f"delta de {delta_pesos:,} COP rompe el señuelo"
    # Y 'motor' debe salir más barato por video que el señuelo.
    assert _unitario("motor") < _unitario("semilla_plus")


def test_solo_un_plan_marcado_como_popular():
    """Von Restorff: si se destacan dos, no se destaca ninguno."""
    populares = [c for c, p in PLANS_BY_CODE.items() if p["popular"]]
    assert populares == ["motor"]


def test_precio_por_video_decrece_con_el_volumen_salvo_el_senuelo():
    """Coherencia del catálogo: a más volumen, menor precio unitario. El señuelo
    es la única excepción deliberada y por eso se excluye."""
    escalera = ["semilla", "motor", "escala"]
    unitarios = [_unitario(c) for c in escalera]
    assert unitarios == sorted(unitarios, reverse=True), unitarios


def test_anual_cobra_diez_meses():
    for code in PLANS_BY_CODE:
        mensual = _periodo(code, MONTHLY)
        anual = _periodo(code, ANNUAL)
        assert anual == mensual * ANNUAL_MONTHS_CHARGED
    assert ANNUAL_MONTHS_CHARGED == 10


def test_diagnostico_es_menor_que_el_plan_de_entrada():
    """El diagnóstico es el escalón de entrada: si cuesta más que el plan más
    barato deja de filtrar y se convierte en una barrera."""
    entrada = min(p["amount_in_cents"] for p in PLANS_BY_CODE.values())
    assert 0 < DIAGNOSTICO_AMOUNT_IN_CENTS < entrada


def test_price_per_video_con_plan_invalido():
    assert price_per_video_in_cents("no_existe") is None
    assert period_amount_in_cents("no_existe") is None
