-- PhinodIA — modelo de datos de suscripciones (auto-cobro Wompi 3RI).
-- Aplicar en Supabase → SQL Editor. Es ADITIVO: no toca `users` ni nada
-- existente. Seguro de correr varias veces (IF NOT EXISTS en todo).
--
-- RLS deny-all: solo el backend (service_role) lee/escribe estas tablas.
-- La anon key expuesta en el frontend NO puede ver datos de pago.

-- ── Suscripciones ───────────────────────────────────────────────────────────
create table if not exists subscriptions (
  id                    uuid primary key default gen_random_uuid(),
  user_id               uuid not null,               -- FK lógica a users.id
  plan_code             text not null,               -- 'emprendedor'|'tienda'|'escala'
  billing_interval      text not null default 'monthly',   -- 'monthly'|'annual'
  status                text not null default 'incomplete',
                          -- incomplete: creada, aún sin fuente 3DS AVAILABLE
                          -- active | past_due | paused | canceled
  -- Fuente de pago Wompi (tarjeta tokenizada + autorizada con 3DS)
  payment_source_id     bigint,                      -- id numérico de Wompi
  payment_source_type   text,                        -- 'CARD'|'NEQUI'
  card_brand            text,                        -- 'mastercard'|'visa'  (3RI = solo mastercard)
  customer_email        text not null,
  -- Ciclo de facturación
  current_period_start  timestamptz,
  current_period_end    timestamptz,
  next_charge_at        timestamptz,                 -- el cron cobra cuando now() >= next_charge_at
  -- Dunning (reintentos ante cobro fallido)
  failed_attempts       int  not null default 0,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  canceled_at           timestamptz
);

-- El cron de cobro escanea exactamente por esto: activas/morosas cuyo período venció.
create index if not exists idx_subscriptions_due
  on subscriptions (status, next_charge_at)
  where status in ('active', 'past_due');

create index if not exists idx_subscriptions_user on subscriptions (user_id);

-- ── Facturas por período (idempotencia + dunning) ───────────────────────────
-- Una factura por período. `reference` único = un reintento del cron NO cobra
-- dos veces. `credited` = un webhook duplicado NO acredita créditos dos veces.
create table if not exists subscription_invoices (
  id                    uuid primary key default gen_random_uuid(),
  subscription_id       uuid not null references subscriptions(id) on delete cascade,
  user_id               uuid not null,
  reference             text not null unique,        -- 'PH-SUB-{sub_id8}-{YYYYMM}'
  amount_in_cents       bigint not null,
  currency              text not null default 'COP',
  wompi_transaction_id  text,
  status                text not null default 'pending',  -- pending|approved|declined|error|voided
  attempt               int  not null default 1,
  credited              boolean not null default false,
  created_at            timestamptz not null default now(),
  paid_at               timestamptz
);

create index if not exists idx_invoices_subscription on subscription_invoices (subscription_id);
create index if not exists idx_invoices_status       on subscription_invoices (status);

-- ── Seguridad: RLS deny-all (solo service_role accede) ──────────────────────
alter table subscriptions        enable row level security;
alter table subscription_invoices enable row level security;
-- Sin políticas para anon/authenticated = deny-all. El backend usa la
-- service_key, que hace bypass de RLS. Datos de pago quedan invisibles al
-- frontend aunque se filtre la anon key.
