-- Trade Slip Dashboard — multi-broker schema.
-- Run in the Supabase SQL editor (new projects).
-- Existing projects: run migration_multi_broker.sql instead.
--
-- Storage: private bucket named trade-slips
-- Template: trade-slips/templates/blank-trade-slip.pdf
-- Slip PDFs: {broker_id}/{year}/{month}/{day}/{client_code}_{date}.pdf
--
-- Auth: create users via Admin invite (or Supabase Auth → Users for the first admin).
-- Set ADMIN_BOOTSTRAP_EMAIL in the app env to the first admin's email so their
-- brokers row is created automatically on first login.

CREATE TABLE IF NOT EXISTS brokers (
  id UUID PRIMARY KEY,  -- matches auth.users.id
  email TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT 'broker' CHECK (role IN ('admin', 'broker')),
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_brokers_email ON brokers (email);
CREATE INDEX IF NOT EXISTS idx_brokers_active ON brokers (is_active);

CREATE TABLE IF NOT EXISTS daily_trade_slips (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_id UUID NOT NULL REFERENCES brokers (id),
  client_code TEXT NOT NULL,
  client_name TEXT NOT NULL,
  trade_date DATE NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('Unsigned', 'Signed')),
  public_url TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (broker_id, client_code, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_daily_trade_slips_broker_date
  ON daily_trade_slips (broker_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_daily_trade_slips_broker_status
  ON daily_trade_slips (broker_id, status);
