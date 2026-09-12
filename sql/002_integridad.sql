-- PhinodIA — integridad e índices que faltaban (2026-09-12).
-- Aplicar en Supabase → SQL Editor. Seguro de correr varias veces.
--
-- Contexto: el código de pagos da por hecho un UNIQUE sobre
-- transactions.wompi_transaction_id ("UNIQUE-collision race" dice el
-- comentario). NO EXISTÍA. Y ya había causado daño real: dos clientes
-- recibieron el doble de créditos por un solo pago, en diciembre de 2025.

begin;

-- ── 1. Desduplicar antes de poder indexar ──────────────────────────────────
-- NO se borran las filas: son el registro contable de lo que pasó de verdad.
-- Se marca la segunda entrega para que el id quede único y el histórico siga
-- contando la historia completa.
update transactions t
   set wompi_transaction_id = t.wompi_transaction_id || '#entrega-duplicada-' || t.id
 where exists (
   select 1 from transactions p
    where p.wompi_transaction_id = t.wompi_transaction_id
      and (p.created_at < t.created_at
           or (p.created_at = t.created_at and p.id < t.id)))
   and t.wompi_transaction_id not like '%#entrega-duplicada-%';

-- ── 2. La restricción que sostenía toda la idempotencia de pagos ───────────
create unique index if not exists ux_transactions_wompi_tx
  on transactions (wompi_transaction_id);

-- ── 3. Índices de las consultas calientes ──────────────────────────────────
-- transactions solo tenía su clave primaria: cada búsqueda de webhook
-- (where wompi_transaction_id = ...) recorría la tabla entera.
create index if not exists ix_transactions_user on transactions (user_id);

-- /mis-generaciones: user_id = X order by created_at desc
create index if not exists ix_jobs_user_fecha on jobs (user_id, created_at desc);

-- El barrido de trabajos huérfanos del arranque:
--   status in ('processing','generating') and created_at < corte
-- Parcial: solo indexa lo que el barrido mira, así ocupa casi nada.
create index if not exists ix_jobs_en_vuelo on jobs (created_at)
  where status in ('processing', 'generating');

-- La reconciliación de reembolsos pendientes (resolved_at is null).
create index if not exists ix_pending_refunds_sin_resolver on pending_refunds (id)
  where resolved_at is null;

commit;

-- ── 4. Relación que faltaba ────────────────────────────────────────────────
-- pending_refunds.user_id no tenía clave foránea: se podían quedar reembolsos
-- apuntando a un usuario que ya no existe. NOT VALID para no bloquear la tabla
-- con las filas históricas; se valida aparte.
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'fk_pending_refunds_user') then
    alter table pending_refunds
      add constraint fk_pending_refunds_user
      foreign key (user_id) references users(id) on delete cascade not valid;
  end if;
end $$;
