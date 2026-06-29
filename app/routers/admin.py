"""Admin endpoints — sales tracking + CSV export.

Auth: simple shared-secret token via ?token=XXX query param. Set
ADMIN_TOKEN env var; if empty, admin is disabled (404).

Why a token instead of a cookie/login: this is a single-admin tool
(Sergio). Token via URL is friction-free for him to bookmark and
download CSV. Anyone scanning URLs without the token gets 401."""
from __future__ import annotations

import csv
import hmac
import io
import logging
from datetime import datetime, timezone
from html import escape as _esc_html
import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse, Response
from pydantic import BaseModel, EmailStr, Field

from app.config import get_settings
from app.database import db
from app.services.gmail import GmailSender, build_ops_alert_email
from app.services.credits import credits_for_service_label

router = APIRouter()
logger = logging.getLogger(__name__)


class OpsEmailRequest(BaseModel):
    subject: str = Field(..., min_length=3, max_length=180)
    title: str = Field(..., min_length=3, max_length=180)
    summary: str = Field("", max_length=500)
    severity: Literal["info", "warning", "critical"] = "info"
    to: EmailStr | None = None
    facts: dict[str, str] = Field(default_factory=dict)
    items: list[str] = Field(default_factory=list)
    raw_text: str = Field("", max_length=12000)


def _check_token(token: str) -> None:
    s = get_settings()
    if not s.admin_token:
        raise HTTPException(404, "Admin not configured")
    if not token or not hmac.compare_digest(token, s.admin_token):
        raise HTTPException(401, "Invalid admin token")


async def _fetch_sales(days: int = 90) -> list[dict]:
    """All APPROVED transactions in last N days, joined with user emails.

    Limited to 1000 — beyond that, paginate. PhinodIA at current scale
    won't hit this for years."""
    txs = await db.select(
        "transactions",
        {"status": "eq.APPROVED", "order": "created_at.desc"},
    )
    # Cap to most recent N
    cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
    recent = []
    for tx in txs[:1000]:
        try:
            ts = datetime.fromisoformat(tx["created_at"].replace("Z", "+00:00")).timestamp()
            if ts < cutoff:
                continue
        except Exception:
            pass
        recent.append(tx)

    # Join with users to get emails
    user_ids = sorted({tx["user_id"] for tx in recent if tx.get("user_id")})
    emails_by_uid: dict[str, str] = {}
    if user_ids:
        # Supabase 'in' filter has length limit; chunk by 50
        for i in range(0, len(user_ids), 50):
            chunk = user_ids[i : i + 50]
            in_clause = "in.(" + ",".join(f'"{u}"' for u in chunk) + ")"
            users = await db.select("users", {"id": in_clause})
            for u in users:
                emails_by_uid[u["id"]] = u.get("email", "")
    rows = []
    for tx in recent:
        rows.append({
            "fecha": tx.get("created_at", "")[:19].replace("T", " "),
            "email": emails_by_uid.get(tx.get("user_id", ""), ""),
            "plan": tx.get("plan_name", ""),
            "creditos": tx.get("credits_added", 0),
            "monto_cop": tx.get("amount_cop", 0) / 100,
            "monto_centavos": tx.get("amount_cop", 0),
            "wompi_tx_id": tx.get("wompi_transaction_id", ""),
            "transaction_id": tx.get("id", ""),
            "user_id": tx.get("user_id", ""),
        })
    return rows


@router.get("/sales.csv")
async def sales_csv(token: str = Query(...), days: int = Query(90, ge=1, le=730)):
    """Download all approved sales as CSV. Open in Excel / Google Sheets."""
    _check_token(token)
    rows = await _fetch_sales(days)

    buf = io.StringIO()
    fieldnames = [
        "fecha", "email", "plan", "creditos", "monto_cop", "monto_centavos",
        "wompi_tx_id", "transaction_id", "user_id",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

    csv_text = buf.getvalue()
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="phinodia_ventas_{datetime.now().strftime("%Y%m%d_%H%M")}.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/sales.json")
async def sales_json(token: str = Query(...), days: int = Query(90, ge=1, le=730)):
    """Same data as CSV but in JSON for any downstream tool / dashboard."""
    _check_token(token)
    rows = await _fetch_sales(days)
    total = sum(r["monto_cop"] for r in rows)
    return {
        "ventas": rows,
        "totales": {
            "count": len(rows),
            "ingresos_cop": total,
            "ticket_promedio_cop": (total / len(rows)) if rows else 0,
            "periodo_dias": days,
            "generado_at": datetime.now(timezone.utc).isoformat(),
        },
    }


@router.get("/sales", response_class=HTMLResponse)
async def sales_dashboard(token: str = Query(...), days: int = Query(90, ge=1, le=730)):
    """HTML dashboard with sales table + KPIs + download buttons.

    Renders inline (no separate static files) so the URL is shareable
    + works behind a token without static-asset auth complexity."""
    _check_token(token)
    rows = await _fetch_sales(days)
    total = sum(r["monto_cop"] for r in rows)
    avg = (total / len(rows)) if rows else 0

    # Aggregate by plan
    by_plan: dict[str, dict] = {}
    for r in rows:
        plan = r["plan"]
        if plan not in by_plan:
            by_plan[plan] = {"count": 0, "revenue": 0, "credits": 0}
        by_plan[plan]["count"] += 1
        by_plan[plan]["revenue"] += r["monto_cop"]
        by_plan[plan]["credits"] += r["creditos"]

    rows_html = "\n".join(
        f"<tr><td>{_esc_html(str(r['fecha']))}</td><td>{_esc_html(str(r['email']))}</td><td>{_esc_html(str(r['plan']))}</td>"
        f"<td>{r['creditos']}</td><td class='money'>${r['monto_cop']:,.0f}</td>"
        f"<td class='mono'>{_esc_html(str(r['wompi_tx_id']))}</td></tr>"
        for r in rows
    )
    plan_rows_html = "\n".join(
        f"<tr><td>{_esc_html(str(plan))}</td><td>{stats['count']}</td>"
        f"<td>{stats['credits']}</td><td class='money'>${stats['revenue']:,.0f}</td></tr>"
        for plan, stats in sorted(by_plan.items(), key=lambda x: -x[1]['revenue'])
    )

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>PhinodIA · Ventas — Dashboard</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 0; padding: 32px; background: #FAFBF7; color: #2A2A2A; }}
  h1 {{ color: #0B1437; margin: 0 0 8px; font-size: 28px; }}
  .subtitle {{ color: #6B7280; margin-bottom: 32px; font-size: 14px; }}
  .kpis {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 32px; }}
  .kpi {{ background: white; border-radius: 12px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
  .kpi .label {{ color: #6B7280; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; }}
  .kpi .value {{ font-size: 28px; font-weight: 700; color: #0B1437; }}
  .actions {{ margin-bottom: 24px; display: flex; gap: 12px; }}
  .btn {{ background: #3B82F6; color: white; padding: 10px 20px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 14px; }}
  .btn:hover {{ background: #2563EB; }}
  .btn-secondary {{ background: #6B7280; }}
  table {{ width: 100%; background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.05); border-collapse: collapse; margin-bottom: 32px; }}
  th, td {{ padding: 12px 16px; text-align: left; border-bottom: 1px solid #E5E7EB; font-size: 14px; }}
  th {{ background: #0B1437; color: white; font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover {{ background: #F9FAFB; }}
  .money {{ font-weight: 600; color: #0B1437; text-align: right; font-variant-numeric: tabular-nums; }}
  .mono {{ font-family: 'SF Mono', Menlo, monospace; font-size: 12px; color: #6B7280; }}
  h2 {{ color: #0B1437; margin: 24px 0 16px; font-size: 20px; }}
  .empty {{ text-align: center; padding: 48px; color: #6B7280; }}
</style>
</head>
<body>
  <h1>📊 PhinodIA · Ventas</h1>
  <p class="subtitle">Últimos {days} días · Generado {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</p>

  <div class="kpis">
    <div class="kpi"><div class="label">Ingresos totales</div><div class="value">${total:,.0f}</div></div>
    <div class="kpi"><div class="label">Ventas</div><div class="value">{len(rows)}</div></div>
    <div class="kpi"><div class="label">Ticket promedio</div><div class="value">${avg:,.0f}</div></div>
    <div class="kpi"><div class="label">Planes vendidos</div><div class="value">{len(by_plan)}</div></div>
  </div>

  <div class="actions">
    <a class="btn" href="/api/v1/admin/sales.csv?token={token}&days={days}">📥 Descargar CSV (Excel)</a>
    <a class="btn btn-secondary" href="/api/v1/admin/sales.json?token={token}&days={days}">📋 JSON</a>
    <a class="btn btn-secondary" href="?token={token}&days=7">7d</a>
    <a class="btn btn-secondary" href="?token={token}&days=30">30d</a>
    <a class="btn btn-secondary" href="?token={token}&days=90">90d</a>
    <a class="btn btn-secondary" href="?token={token}&days=365">365d</a>
  </div>

  <h2>Ventas por plan</h2>
  <table>
    <tr><th>Plan</th><th>Cantidad</th><th>Créditos</th><th>Ingresos COP</th></tr>
    {plan_rows_html or '<tr><td colspan="4" class="empty">Sin datos</td></tr>'}
  </table>

  <h2>Detalle de ventas ({len(rows)})</h2>
  <table>
    <tr><th>Fecha (UTC)</th><th>Email</th><th>Plan</th><th>Créditos</th><th>Monto</th><th>Wompi TX</th></tr>
    {rows_html or '<tr><td colspan="6" class="empty">Sin ventas en este periodo</td></tr>'}
  </table>
</body>
</html>"""
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


@router.post("/ops-email")
async def ops_email(req: OpsEmailRequest, token: str = Query(...)):
    """Send a polished internal ops alert email through Gmail OAuth.

    Shared-secret protected so companion services like the ads bot can
    reuse PhinodIA's already-working Gmail integration without duplicating
    OAuth secrets across multiple EasyPanel services.
    """
    _check_token(token)
    settings = get_settings()
    sender = GmailSender(
        client_id=settings.gmail_client_id,
        client_secret=settings.gmail_client_secret,
        refresh_token=settings.gmail_refresh_token,
        sender_email=settings.gmail_sender_email,
    )
    subject, html_body = build_ops_alert_email(
        subject=req.subject,
        title=req.title,
        summary=req.summary,
        severity=req.severity,
        facts=req.facts,
        items=req.items,
        raw_text=req.raw_text,
    )
    recipient = req.to or settings.gmail_sender_email
    # Anti open-relay: aunque el endpoint esta protegido por token, si el token se
    # filtra no debe poder enviar a destinatarios arbitrarios. Restringe a
    # @phinodia.com + el remitente + la allowlist configurada.
    _allowed = {settings.gmail_sender_email.lower()}
    _allowed |= {a.strip().lower() for a in (getattr(settings, "ops_email_allowlist", "") or "").split(",") if a.strip()}
    if not (recipient.lower() in _allowed or recipient.lower().endswith("@phinodia.com")):
        logger.warning("ops-email rechazado: destinatario %r no permitido (configura OPS_EMAIL_ALLOWLIST si es legitimo)", recipient)
        raise HTTPException(403, "Destinatario no permitido")
    await asyncio.to_thread(
        sender.send_email,
        to=recipient,
        subject=subject,
        html_body=html_body,
    )
    return {"status": "ok", "to": recipient, "subject": subject}


# ── Activación / funnel dashboard ────────────────────────────────────
# La fuga #1 del negocio: la gente compra créditos y no crea el video.
# Este dashboard mide el funnel server-side (registro → compra → activación)
# + uso de créditos + éxito de generación + recordatorios enviados.
# OJO: el CVR real (visitas→compra) vive en Meta (no tenemos tráfico server-side);
# aquí mostramos lo accionable que SÍ tenemos.
async def _fetch_activation_metrics() -> dict:
    users = await db.select("users", {"select": "id,credits", "limit": "10000"})
    jobs = await db.select("jobs", {"select": "user_id,status,service_type", "limit": "50000"})
    txs = await db.select("transactions", {"select": "user_id,credits_added,status,amount_cop", "limit": "10000"})
    reminders = await db.select("activation_emails", {"select": "kind", "limit": "10000"})

    total_users = len(users)
    credits_balance = sum(float(u.get("credits") or 0) for u in users)
    with_credits = sum(1 for u in users if float(u.get("credits") or 0) > 0)

    approved = [t for t in txs if t.get("status") == "APPROVED"]
    buyer_ids = {t["user_id"] for t in approved if t.get("user_id")}
    credits_sold = sum(int(t.get("credits_added") or 0) for t in approved)
    revenue_cop = sum(int(t.get("amount_cop") or 0) for t in approved) / 100
    # Otorgados = créditos que entraron a las billeteras (ventas APPROVED + bonos de
    # referido REFERRAL_BONUS). El saldo (users.credits) incluye los bonos, así que
    # "usados" debe restarse de los OTORGADOS, no de lo vendido, o subestima el uso.
    GRANT_STATES = {"APPROVED", "REFERRAL_BONUS"}
    credits_granted = sum(int(t.get("credits_added") or 0) for t in txs if t.get("status") in GRANT_STATES)
    # Consumo REAL = costo de las generaciones completadas. NO usamos
    # "otorgados − saldo": el saldo trae créditos de welcome/prueba históricos (y
    # manuales) que NO están en transactions, lo que inflaba el saldo por encima de
    # lo otorgado y rompía la métrica (daba >100% "sin usar").
    credits_consumed = sum(
        credits_for_service_label(j.get("service_type") or "")
        for j in jobs if j.get("status") == "completed"
    )

    completed_ids = {j["user_id"] for j in jobs if j.get("status") == "completed" and j.get("user_id")}
    activated_buyers = buyer_ids & completed_ids
    jobs_total = len(jobs)
    jobs_completed = sum(1 for j in jobs if j.get("status") == "completed")
    jobs_failed = sum(1 for j in jobs if j.get("status") == "failed")

    rem_by_kind: dict[str, int] = {}
    for r in reminders:
        k = r.get("kind") or "?"
        rem_by_kind[k] = rem_by_kind.get(k, 0) + 1

    return {
        "usuarios": total_users,
        "compradores": len(buyer_ids),
        "con_creditos": with_credits,
        "activados": len(activated_buyers),
        "sin_activar": len(buyer_ids - completed_ids),
        "tasa_activacion_pct": (len(activated_buyers) / len(buyer_ids) * 100) if buyer_ids else 0.0,
        "creditos_vendidos": credits_sold,
        "creditos_otorgados": credits_granted,
        "creditos_saldo": int(credits_balance),
        "creditos_consumidos": credits_consumed,
        # % de créditos VENDIDOS que aún no se han consumido (clamp 0-100). Mide la
        # fuga: de lo que pagaron, cuánto sigue sin usarse.
        "pct_creditos_sin_usar": max(0.0, min(100.0, (credits_sold - credits_consumed) / credits_sold * 100)) if credits_sold else 0.0,
        "ingresos_cop": revenue_cop,
        "jobs_total": jobs_total,
        "jobs_completados": jobs_completed,
        "jobs_fallidos": jobs_failed,
        "tasa_exito_generacion_pct": (jobs_completed / jobs_total * 100) if jobs_total else 0.0,
        "recordatorios_enviados": rem_by_kind,
        "recordatorios_total": sum(rem_by_kind.values()),
        "generado_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/activacion.json")
async def activacion_json(token: str = Query(...)):
    """Métricas de activación/funnel en JSON."""
    _check_token(token)
    return await _fetch_activation_metrics()


@router.get("/activacion", response_class=HTMLResponse)
async def activacion_dashboard(token: str = Query(...)):
    """Dashboard de activación: funnel registro→compra→activación + uso de créditos."""
    _check_token(token)
    m = await _fetch_activation_metrics()

    def pct(n, d):
        return (n / d * 100) if d else 0.0
    # Funnel widths (relativos al máximo = usuarios)
    base = max(m["usuarios"], 1)
    funnel = [
        ("Usuarios registrados", m["usuarios"], "#0B1437"),
        ("Compradores", m["compradores"], "#1E5BC6"),
        ("Compradores con créditos sin usar", m["sin_activar"], "#C2410C"),
        ("Activados (crearon ≥1 video)", m["activados"], "#1E9E62"),
    ]
    funnel_html = "".join(
        f'<div class="frow"><div class="flabel">{_esc_html(lbl)}</div>'
        f'<div class="fbar-wrap"><div class="fbar" style="width:{max(2,pct(n,base)):.0f}%;background:{col}">{n}</div></div></div>'
        for lbl, n, col in funnel
    )
    rem = m["recordatorios_enviados"]
    rem_labels = {"reminder_d1": "Día 1", "reminder_d3": "Día 3", "reminder_d7": "Día 7", "winback": "Win-back"}
    rem_html = "".join(
        f"<tr><td>{_esc_html(rem_labels.get(k, k))}</td><td style='text-align:right'>{v}</td></tr>"
        for k, v in sorted(rem.items())
    ) or '<tr><td colspan="2" class="empty">Aún no se han enviado recordatorios</td></tr>'

    act_color = "#1E9E62" if m["tasa_activacion_pct"] >= 30 else ("#C2410C" if m["tasa_activacion_pct"] >= 10 else "#B42318")
    gen_color = "#1E9E62" if m["tasa_exito_generacion_pct"] >= 80 else ("#C2410C" if m["tasa_exito_generacion_pct"] >= 50 else "#B42318")

    html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8">
<title>PhinodIA · Activación — Dashboard</title>
<style>
  body {{ font-family:-apple-system,system-ui,sans-serif; margin:0; padding:32px; background:#FAFBF7; color:#2A2A2A; }}
  h1 {{ color:#0B1437; margin:0 0 8px; font-size:28px; }}
  .subtitle {{ color:#6B7280; margin-bottom:28px; font-size:14px; }}
  .kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:16px; margin-bottom:28px; }}
  .kpi {{ background:white; border-radius:12px; padding:20px; box-shadow:0 1px 3px rgba(0,0,0,0.05); }}
  .kpi .label {{ color:#6B7280; font-size:12px; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:8px; }}
  .kpi .value {{ font-size:28px; font-weight:700; color:#0B1437; }}
  .actions {{ margin-bottom:24px; display:flex; gap:12px; flex-wrap:wrap; }}
  .btn {{ background:#3B82F6; color:white; padding:10px 20px; border-radius:8px; text-decoration:none; font-weight:600; font-size:14px; }}
  .btn-secondary {{ background:#6B7280; }}
  h2 {{ color:#0B1437; margin:28px 0 16px; font-size:20px; }}
  .frow {{ display:flex; align-items:center; gap:14px; margin-bottom:10px; }}
  .flabel {{ width:280px; font-size:13px; color:#374151; text-align:right; }}
  .fbar-wrap {{ flex:1; background:#EEF1F4; border-radius:8px; overflow:hidden; }}
  .fbar {{ color:white; font-weight:700; font-size:13px; padding:10px 12px; border-radius:8px; min-width:28px; box-sizing:border-box; white-space:nowrap; }}
  table {{ width:100%; max-width:420px; background:white; border-radius:12px; overflow:hidden; box-shadow:0 1px 3px rgba(0,0,0,0.05); border-collapse:collapse; }}
  th,td {{ padding:12px 16px; text-align:left; border-bottom:1px solid #E5E7EB; font-size:14px; }}
  th {{ background:#0B1437; color:white; font-size:12px; text-transform:uppercase; letter-spacing:0.05em; }}
  tr:last-child td {{ border-bottom:none; }}
  .empty {{ text-align:center; color:#6B7280; }}
  .note {{ margin-top:24px; padding:14px 16px; background:#eef6ff; border-left:4px solid #3B82F6; border-radius:6px; font-size:13px; color:#374151; max-width:760px; }}
</style></head><body>
  <h1>🎯 PhinodIA · Activación</h1>
  <p class="subtitle">Funnel y uso de créditos · Generado {m['generado_at'][:19]} UTC</p>

  <div class="kpis">
    <div class="kpi"><div class="label">Tasa de activación</div><div class="value" style="color:{act_color}">{m['tasa_activacion_pct']:.0f}%</div></div>
    <div class="kpi"><div class="label">Créditos sin usar</div><div class="value">{m['pct_creditos_sin_usar']:.0f}%</div></div>
    <div class="kpi"><div class="label">Éxito de generación</div><div class="value" style="color:{gen_color}">{m['tasa_exito_generacion_pct']:.0f}%</div></div>
    <div class="kpi"><div class="label">Recordatorios enviados</div><div class="value">{m['recordatorios_total']}</div></div>
  </div>

  <div class="actions">
    <a class="btn btn-secondary" href="/api/v1/admin/sales?token={token}">📊 Ventas</a>
    <a class="btn btn-secondary" href="/api/v1/admin/ab-test?token={token}">🧪 A/B</a>
    <a class="btn" href="/api/v1/admin/activacion.json?token={token}">📋 JSON</a>
  </div>

  <h2>Funnel</h2>
  {funnel_html}

  <h2>Créditos</h2>
  <div class="kpis">
    <div class="kpi"><div class="label">Vendidos</div><div class="value">{m['creditos_vendidos']:,}</div></div>
    <div class="kpi"><div class="label">En saldo</div><div class="value">{m['creditos_saldo']:,}</div></div>
    <div class="kpi"><div class="label">Consumidos</div><div class="value">{m['creditos_consumidos']:,}</div></div>
    <div class="kpi"><div class="label">Ingresos</div><div class="value">${m['ingresos_cop']:,.0f}</div></div>
  </div>
  {f'<div class="note" style="background:#FFF7ED;border-left-color:#C2410C">⚠️ El <b>saldo</b> ({m["creditos_saldo"]:,}) supera por mucho lo <b>vendido</b> ({m["creditos_vendidos"]:,}): hay ~{m["creditos_saldo"]-m["creditos_otorgados"]:,} créditos de <b>prueba/welcome históricos</b> (o ajustes manuales) en las billeteras, no de compras. Por eso "consumidos" se calcula del costo real de las generaciones, no de "otorgados − saldo".</div>' if m['creditos_saldo'] > m['creditos_otorgados'] * 1.2 else ''}

  <h2>Recordatorios de activación</h2>
  <table><tr><th>Tipo</th><th style="text-align:right">Enviados</th></tr>{rem_html}</table>

  <div class="note">
    <b>Sobre el CVR (visitas → compra):</b> el conversion rate real necesita el tráfico, que vive en
    Meta/Pixel (no server-side). Cuando enciendas pauta, mídelo en Ads Manager (ViewContent→Purchase) o
    en GA4. Aquí ves el funnel <i>después</i> de la compra — donde está tu fuga real: de
    {m['compradores']} compradores, solo {m['activados']} activaron ({m['tasa_activacion_pct']:.0f}%).
  </div>
</body></html>"""
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


# ── A/B test dashboard ───────────────────────────────────────────────
# Aggregates transactions by variant encoded in plan_name (format:
# "<service>_<credits>__ab_<letter>" or just "<service>_<credits>" for
# control/no-test). Frontend assigns variant on first visit to /precios
# (test id precios_cta_v1) and the variant rides through the checkout →
# reference suffix → webhook → plan_name encoding.

def _extract_variant(plan_name: str) -> tuple[str, str]:
    """Returns (plan_without_variant, variant_or_empty)."""
    if "__ab_" in plan_name:
        base, suffix = plan_name.rsplit("__ab_", 1)
        variant = suffix[:1] if suffix else ""
        return base, variant
    return plan_name, ""


async def _ab_aggregate(test_id: str, days: int) -> dict:
    rows = await _fetch_sales(days=days)
    # Group by variant. 'none' captures rows with no variant suffix
    # (either pre-AB-test rows or users who visited before rollout).
    agg: dict[str, dict] = {}
    for r in rows:
        _, variant = _extract_variant(r.get("plan", ""))
        key = variant or "none"
        if key not in agg:
            agg[key] = {
                "variant": key,
                "conversions": 0,
                "revenue_cop": 0.0,
                "plans": {},
            }
        agg[key]["conversions"] += 1
        agg[key]["revenue_cop"] += float(r.get("monto_cop", 0))
        base_plan, _ = _extract_variant(r.get("plan", ""))
        agg[key]["plans"][base_plan] = agg[key]["plans"].get(base_plan, 0) + 1
    # Compute averages
    for v in agg.values():
        v["avg_ticket_cop"] = (v["revenue_cop"] / v["conversions"]) if v["conversions"] else 0
    # Stable ordering — 'a' first, then 'b', then the rest
    ordered = sorted(agg.values(), key=lambda x: (x["variant"] != "a", x["variant"] != "b", x["variant"]))
    return {
        "test_id": test_id,
        "period_days": days,
        "variants": ordered,
        "total_conversions": sum(v["conversions"] for v in ordered),
        "total_revenue_cop": sum(v["revenue_cop"] for v in ordered),
        "generado_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ab-test.json")
async def ab_test_json(
    token: str = Query(...),
    test_id: str = Query("precios_cta_v1"),
    days: int = Query(30, ge=1, le=365),
):
    """Aggregation for a given A/B test over N days."""
    _check_token(token)
    return await _ab_aggregate(test_id, days)


@router.get("/ab-test", response_class=HTMLResponse)
async def ab_test_dashboard(
    token: str = Query(...),
    test_id: str = Query("precios_cta_v1"),
    days: int = Query(30, ge=1, le=365),
):
    """HTML dashboard — variant comparison with conversion rate lift."""
    _check_token(token)
    d = await _ab_aggregate(test_id, days)
    variants = d["variants"]
    # Find baseline (variant 'a' if present, else first)
    baseline = next((v for v in variants if v["variant"] == "a"), variants[0] if variants else None)
    rows_html = ""
    for v in variants:
        lift_vs_a = ""
        if baseline and baseline["conversions"] > 0 and v["variant"] != baseline["variant"]:
            if v["conversions"] > 0:
                lift_rev = (v["avg_ticket_cop"] - baseline["avg_ticket_cop"]) / baseline["avg_ticket_cop"] * 100
                lift_vs_a = f'<span style="color:{"#080" if lift_rev>=0 else "#a00"}">{lift_rev:+.1f}%</span>'
        plans_str = ", ".join(f'{_esc_html(str(p))}×{n}' for p, n in sorted(v["plans"].items()))
        rows_html += (
            f'<tr><td><b>{_esc_html(str(v["variant"])).upper()}</b></td>'
            f'<td style="text-align:right">{v["conversions"]}</td>'
            f'<td style="text-align:right">${int(v["revenue_cop"]):,}</td>'
            f'<td style="text-align:right">${int(v["avg_ticket_cop"]):,}</td>'
            f'<td style="text-align:right">{lift_vs_a}</td>'
            f'<td style="font-size:12px">{plans_str}</td></tr>'
        )
    html = f"""<!DOCTYPE html>
<html lang="es"><head>
<meta charset="utf-8"><title>PhinodIA A/B — {test_id}</title>
<style>
  body {{ font-family:-apple-system,BlinkMacSystemFont,sans-serif; max-width:900px; margin:32px auto; padding:24px; background:#f5f7fa; color:#0B1437 }}
  h1 {{ color:#0B1437; border-bottom:3px solid #3B82F6; padding-bottom:8px }}
  .meta {{ color:#666; font-size:14px; margin:8px 0 24px }}
  .kpi {{ display:grid; grid-template-columns:repeat(3,1fr); gap:16px; margin:16px 0 }}
  .kpi > div {{ background:white; padding:16px; border-radius:8px; box-shadow:0 1px 3px rgba(0,0,0,0.05) }}
  .kpi .label {{ font-size:12px; color:#666; text-transform:uppercase; letter-spacing:0.05em }}
  .kpi .value {{ font-size:22px; font-weight:700; margin-top:6px }}
  table {{ width:100%; background:white; border-collapse:collapse; border-radius:8px; overflow:hidden; margin-top:16px; box-shadow:0 1px 3px rgba(0,0,0,0.05) }}
  th {{ background:#0B1437; color:white; text-align:left; padding:12px }}
  td {{ padding:12px; border-top:1px solid #eee }}
  tr:nth-child(even) {{ background:#f9fafb }}
  .note {{ margin-top:24px; padding:12px; background:#eef6ff; border-left:4px solid #3B82F6; border-radius:4px; font-size:13px }}
</style></head><body>
<h1>🧪 A/B Test — {test_id}</h1>
<div class="meta">Últimos {d['period_days']} días · generado {d['generado_at'][:19]} UTC</div>
<div class="kpi">
  <div><div class="label">Variantes</div><div class="value">{len(variants)}</div></div>
  <div><div class="label">Conversiones totales</div><div class="value">{d['total_conversions']}</div></div>
  <div><div class="label">Revenue total</div><div class="value">${int(d['total_revenue_cop']):,}</div></div>
</div>
<table><thead><tr>
  <th>Variante</th><th style="text-align:right">Conversiones</th>
  <th style="text-align:right">Revenue COP</th><th style="text-align:right">Ticket promedio</th>
  <th style="text-align:right">Lift vs A</th><th>Planes vendidos</th>
</tr></thead><tbody>
{rows_html or '<tr><td colspan="6">Sin datos todavía — espera que el tráfico acumule conversiones por variante</td></tr>'}
</tbody></table>
<div class="note">
  <b>Interpretación:</b> con menos de ~30 conversiones por variante los resultados no son
  estadísticamente significativos — seguir midiendo hasta cruzar ese umbral. La variante
  'none' agrupa transacciones sin variant suffix (pre-test o tráfico directo).
</div>
</body></html>"""
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})
