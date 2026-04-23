"""Admin endpoints — sales tracking + CSV export.

Auth: simple shared-secret token via ?token=XXX query param. Set
ADMIN_TOKEN env var; if empty, admin is disabled (404).

Why a token instead of a cookie/login: this is a single-admin tool
(Sergio). Token via URL is friction-free for him to bookmark and
download CSV. Anyone scanning URLs without the token gets 401."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse, Response

from app.config import get_settings
from app.database import db

router = APIRouter()


def _check_token(token: str) -> None:
    s = get_settings()
    if not s.admin_token:
        raise HTTPException(404, "Admin not configured")
    if token != s.admin_token:
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
        f"<tr><td>{r['fecha']}</td><td>{r['email']}</td><td>{r['plan']}</td>"
        f"<td>{r['creditos']}</td><td class='money'>${r['monto_cop']:,.0f}</td>"
        f"<td class='mono'>{r['wompi_tx_id']}</td></tr>"
        for r in rows
    )
    plan_rows_html = "\n".join(
        f"<tr><td>{plan}</td><td>{stats['count']}</td>"
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
        plans_str = ", ".join(f'{p}×{n}' for p, n in sorted(v["plans"].items()))
        rows_html += (
            f'<tr><td><b>{v["variant"].upper()}</b></td>'
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
