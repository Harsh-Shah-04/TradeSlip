-- IPO Trading Module (Phase 1)
-- Run in Supabase SQL editor or via migration tooling.

CREATE TABLE IF NOT EXISTS ipo_category_labels (
  code TEXT PRIMARY KEY,
  category_group TEXT CHECK (
    category_group IS NULL
    OR category_group IN ('Retail', 'Small HNI', 'Big HNI', 'Shareholder')
  ),
  display_order INT NOT NULL DEFAULT 0,
  is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS ipo_trades (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_id UUID NOT NULL REFERENCES brokers (id),
  trade_date DATE NOT NULL,
  script TEXT NOT NULL,
  party TEXT NOT NULL,
  category TEXT NOT NULL,
  category_group TEXT,
  buy_app NUMERIC(18, 4) NOT NULL CHECK (buy_app >= 0),
  buy_rate NUMERIC(18, 4) NOT NULL CHECK (buy_rate >= 0),
  buy_amt NUMERIC(18, 4) NOT NULL CHECK (buy_amt >= 0),
  dalal NUMERIC(18, 4),
  sell_app NUMERIC(18, 4) NOT NULL CHECK (sell_app >= 0),
  sell_rate NUMERIC(18, 4) NOT NULL CHECK (sell_rate >= 0),
  sell_amt NUMERIC(18, 4) NOT NULL CHECK (sell_amt >= 0),
  sell_party TEXT NOT NULL,
  applicant_name TEXT NOT NULL DEFAULT '',
  mail TEXT NOT NULL DEFAULT 'Pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ipo_trades_broker_date
  ON ipo_trades (broker_id, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_ipo_trades_broker_script
  ON ipo_trades (broker_id, script);
CREATE INDEX IF NOT EXISTS idx_ipo_trades_broker_party
  ON ipo_trades (broker_id, party);
CREATE INDEX IF NOT EXISTS idx_ipo_trades_broker_sell_party
  ON ipo_trades (broker_id, sell_party);

-- Seed Excel shorthand labels + parent groups (future Category / Sub-Category)
INSERT INTO ipo_category_labels (code, category_group, display_order, is_active) VALUES
  ('15K', 'Retail', 10, TRUE),
  ('2-SHARE', 'Retail', 20, TRUE),
  ('2- SHARE', 'Retail', 21, TRUE),
  ('2+', 'Small HNI', 30, TRUE),
  ('10+', 'Big HNI', 40, TRUE),
  ('SHARE', 'Shareholder', 50, TRUE),
  ('15K (Shareholder)', 'Shareholder', 60, TRUE),
  ('Retail', 'Retail', 5, TRUE),
  ('Small HNI', 'Small HNI', 25, TRUE),
  ('sHNI', 'Small HNI', 26, TRUE),
  ('Big HNI', 'Big HNI', 35, TRUE),
  ('bHNI', 'Big HNI', 36, TRUE),
  ('Shareholder', 'Shareholder', 45, TRUE)
ON CONFLICT (code) DO UPDATE
SET
  category_group = EXCLUDED.category_group,
  display_order = EXCLUDED.display_order,
  is_active = EXCLUDED.is_active;
