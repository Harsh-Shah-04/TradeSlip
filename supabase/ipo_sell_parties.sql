-- Sell Party master — dynamic list used on sell forms and confirmations.

CREATE TABLE IF NOT EXISTS ipo_sell_parties (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'Active'
    CHECK (status IN ('Active', 'Inactive')),
  is_archived BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ipo_sell_parties_name_lower
  ON ipo_sell_parties (lower(name));

CREATE INDEX IF NOT EXISTS idx_ipo_sell_parties_active
  ON ipo_sell_parties (status)
  WHERE is_archived = FALSE;

-- Seed from existing sell party names on trades
INSERT INTO ipo_sell_parties (name, status, is_archived)
SELECT DISTINCT trim(sell_party), 'Active', FALSE
FROM ipo_sells
WHERE trim(sell_party) <> ''
ON CONFLICT DO NOTHING;
