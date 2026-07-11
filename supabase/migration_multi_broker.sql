-- Upgrade an existing single-broker TradeSlip DB to multi-broker.
-- Run once in the Supabase SQL editor.
--
-- Before running: find your admin user id with:
--   SELECT id, email FROM auth.users WHERE lower(email) = 'your@email.com';
-- Then replace YOUR_FATHER_USER_UUID and YOUR_FATHER_EMAIL below.

CREATE TABLE IF NOT EXISTS brokers (
  id UUID PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT 'broker' CHECK (role IN ('admin', 'broker')),
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_brokers_email ON brokers (email);
CREATE INDEX IF NOT EXISTS idx_brokers_active ON brokers (is_active);

INSERT INTO brokers (id, email, display_name, role, is_active)
VALUES (
  'YOUR_FATHER_USER_UUID',
  'YOUR_FATHER_EMAIL',
  'Admin',
  'admin',
  TRUE
)
ON CONFLICT (id) DO UPDATE
SET
  email = EXCLUDED.email,
  role = 'admin',
  is_active = TRUE;

ALTER TABLE daily_trade_slips
  ADD COLUMN IF NOT EXISTS broker_id UUID REFERENCES brokers (id);

UPDATE daily_trade_slips
SET broker_id = 'YOUR_FATHER_USER_UUID'
WHERE broker_id IS NULL;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM daily_trade_slips WHERE broker_id IS NULL) THEN
    RAISE EXCEPTION 'daily_trade_slips still has NULL broker_id — insert admin broker first';
  END IF;
END $$;

ALTER TABLE daily_trade_slips
  ALTER COLUMN broker_id SET NOT NULL;

ALTER TABLE daily_trade_slips
  DROP CONSTRAINT IF EXISTS daily_trade_slips_client_code_trade_date_key;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'daily_trade_slips_broker_id_client_code_trade_date_key'
  ) THEN
    ALTER TABLE daily_trade_slips
      ADD CONSTRAINT daily_trade_slips_broker_id_client_code_trade_date_key
      UNIQUE (broker_id, client_code, trade_date);
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_daily_trade_slips_broker_date
  ON daily_trade_slips (broker_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_daily_trade_slips_broker_status
  ON daily_trade_slips (broker_id, status);

-- Existing storage objects may remain at year/month/day/... (legacy).
-- New uploads use broker_id/year/month/day/.... Both are supported by the app.
