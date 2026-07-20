-- IPO Trading Module v2 — business entities (IPO Master → Positions → Sells)
-- Replaces the Phase-1 single-row buy+sell Excel model.

CREATE TABLE IF NOT EXISTS ipo_master (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  display_name TEXT NOT NULL,
  open_date DATE,
  close_date DATE,
  listing_date DATE,
  status TEXT NOT NULL DEFAULT 'Upcoming'
    CHECK (status IN ('Upcoming', 'Active', 'Closed')),
  notes TEXT NOT NULL DEFAULT '',
  -- Default application amounts (configured once per IPO)
  amount_bhni NUMERIC(18, 4),                 -- bHNI → Sub-Category 10+
  amount_shni NUMERIC(18, 4),                 -- sHNI → Sub-Category 2+
  amount_retail_15k NUMERIC(18, 4),           -- Retail → 15K
  amount_retail_2minus NUMERIC(18, 4),        -- Retail → 2-
  amount_shareholder_15k NUMERIC(18, 4),      -- Shareholder → 15K Shareholder
  amount_shareholder_2minus NUMERIC(18, 4),   -- Shareholder → 2- Shareholder
  is_archived BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ipo_master_status
  ON ipo_master (status)
  WHERE is_archived = FALSE;

CREATE TABLE IF NOT EXISTS ipo_positions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_id UUID NOT NULL REFERENCES brokers (id),
  ipo_id UUID NOT NULL REFERENCES ipo_master (id),
  trade_date DATE NOT NULL,
  party TEXT NOT NULL,
  category TEXT NOT NULL,
  category_group TEXT,
  applicant_name TEXT NOT NULL DEFAULT '',
  buy_app NUMERIC(18, 4) NOT NULL CHECK (buy_app > 0),
  buy_rate NUMERIC(18, 4) NOT NULL CHECK (buy_rate >= 0),
  buy_amt NUMERIC(18, 4) NOT NULL CHECK (buy_amt >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ipo_positions_broker_date
  ON ipo_positions (broker_id, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_ipo_positions_broker_ipo
  ON ipo_positions (broker_id, ipo_id);
CREATE INDEX IF NOT EXISTS idx_ipo_positions_broker_party
  ON ipo_positions (broker_id, party);

CREATE TABLE IF NOT EXISTS ipo_sells (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  broker_id UUID NOT NULL REFERENCES brokers (id),
  position_id UUID NOT NULL REFERENCES ipo_positions (id) ON DELETE CASCADE,
  sell_date DATE NOT NULL,
  sell_app NUMERIC(18, 4) NOT NULL CHECK (sell_app > 0),
  sell_rate NUMERIC(18, 4) NOT NULL CHECK (sell_rate >= 0),
  sell_amt NUMERIC(18, 4) NOT NULL CHECK (sell_amt >= 0),
  sell_party TEXT NOT NULL,
  brokerage NUMERIC(18, 4),
  notes TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ipo_sells_position
  ON ipo_sells (position_id);
CREATE INDEX IF NOT EXISTS idx_ipo_sells_broker_date
  ON ipo_sells (broker_id, sell_date DESC);

-- Keep category labels from Phase 1 if present; recreate seed if needed
CREATE TABLE IF NOT EXISTS ipo_category_labels (
  code TEXT PRIMARY KEY,
  category_group TEXT CHECK (
    category_group IS NULL
    OR category_group IN ('Retail', 'Small HNI', 'Big HNI', 'Shareholder')
  ),
  display_order INT NOT NULL DEFAULT 0,
  is_active BOOLEAN NOT NULL DEFAULT TRUE
);

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

-- Legacy Phase-1 flat table (ipo_trades) is no longer used by the app.
-- Keep it for safety; drop manually later after confirming no needed data:
-- DROP TABLE IF EXISTS ipo_trades;
-- Client Master (Phase 2A) — global for this single business (like IPO Master).
-- Party → many Applicants. Import fields: Party, Applicant Name, PAN, DPID.
-- Category / default amount optional (filled later in app).

CREATE TABLE IF NOT EXISTS ipo_parties (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'Active'
    CHECK (status IN ('Active', 'Inactive')),
  is_archived BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ipo_parties_name_lower
  ON ipo_parties (lower(name));

CREATE INDEX IF NOT EXISTS idx_ipo_parties_active
  ON ipo_parties (status)
  WHERE is_archived = FALSE;

-- Sell Party master (counterparties for sells / broker confirmations)
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

CREATE TABLE IF NOT EXISTS ipo_applicants (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  party_id UUID NOT NULL REFERENCES ipo_parties (id),
  name TEXT NOT NULL,
  pan TEXT NOT NULL DEFAULT '',
  dpid TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT '',
  default_app_amount NUMERIC(18, 4),
  mobile TEXT NOT NULL DEFAULT '',
  email TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'Active'
    CHECK (status IN ('Active', 'Inactive')),
  is_archived BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ipo_applicants_party
  ON ipo_applicants (party_id)
  WHERE is_archived = FALSE;

CREATE INDEX IF NOT EXISTS idx_ipo_applicants_name
  ON ipo_applicants (lower(name));

CREATE INDEX IF NOT EXISTS idx_ipo_applicants_pan
  ON ipo_applicants (upper(pan))
  WHERE pan <> '';

CREATE INDEX IF NOT EXISTS idx_ipo_applicants_dpid
  ON ipo_applicants (upper(dpid))
  WHERE dpid <> '';

CREATE INDEX IF NOT EXISTS idx_ipo_applicants_party_archived
  ON ipo_applicants (party_id, is_archived);

-- Link buy positions to Client Master (keep text party/applicant_name for history)
ALTER TABLE ipo_positions
  ADD COLUMN IF NOT EXISTS party_id UUID REFERENCES ipo_parties (id);

ALTER TABLE ipo_positions
  ADD COLUMN IF NOT EXISTS applicant_id UUID REFERENCES ipo_applicants (id);

CREATE INDEX IF NOT EXISTS idx_ipo_positions_party_id
  ON ipo_positions (party_id);

CREATE INDEX IF NOT EXISTS idx_ipo_positions_applicant_id
  ON ipo_positions (applicant_id);

-- Sell → applicants (many) for confirmations / reporting
CREATE TABLE IF NOT EXISTS ipo_sell_applicants (
  sell_id UUID NOT NULL REFERENCES ipo_sells (id) ON DELETE CASCADE,
  applicant_id UUID NOT NULL REFERENCES ipo_applicants (id),
  PRIMARY KEY (sell_id, applicant_id)
);

CREATE INDEX IF NOT EXISTS idx_ipo_sell_applicants_applicant
  ON ipo_sell_applicants (applicant_id);

-- Client Master DDL also in ipo_client_master.sql

-- Trade Allocation: applicants assigned to a buy position after buy/sell.
-- Count of allocated applicants must equal BUY APP for Fully Allocated.

CREATE TABLE IF NOT EXISTS ipo_position_allocations (
  position_id UUID NOT NULL REFERENCES ipo_positions (id) ON DELETE CASCADE,
  applicant_id UUID NOT NULL REFERENCES ipo_applicants (id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (position_id, applicant_id)
);

CREATE INDEX IF NOT EXISTS idx_ipo_position_allocations_applicant
  ON ipo_position_allocations (applicant_id);
-- Category / Sub-Category split for IPO positions.
-- Existing rows: category (e.g. 15K) → sub_category; category → 'IPO Application'.

ALTER TABLE ipo_positions
  ADD COLUMN IF NOT EXISTS sub_category TEXT NOT NULL DEFAULT '';

-- Migrate legacy single Category field into Sub-Category, then set main Category.
UPDATE ipo_positions
SET
  sub_category = CASE
    WHEN COALESCE(sub_category, '') <> '' THEN sub_category
    WHEN upper(trim(category)) IN ('2-SHARE', '2- SHARE', '2-') THEN '2-'
    WHEN upper(trim(category)) IN ('15K (SHAREHOLDER)', '15K SHAREHOLDER') THEN '15K Shareholder'
    WHEN upper(trim(category)) IN ('2- SHAREHOLDER', '2-SHAREHOLDER', 'SHARE') THEN '2- Shareholder'
    ELSE trim(category)
  END,
  category = CASE
    WHEN category IN ('IPO Application', 'Premium', 'Subject 2') THEN category
    ELSE 'IPO Application'
  END
WHERE COALESCE(sub_category, '') = ''
   OR category NOT IN ('IPO Application', 'Premium', 'Subject 2');

-- Second pass: any remaining non-main category values still look like sub-codes
UPDATE ipo_positions
SET
  sub_category = trim(category),
  category = 'IPO Application'
WHERE category NOT IN ('IPO Application', 'Premium', 'Subject 2');

CREATE INDEX IF NOT EXISTS idx_ipo_positions_trade_category
  ON ipo_positions (category);

CREATE INDEX IF NOT EXISTS idx_ipo_positions_sub_category
  ON ipo_positions (sub_category);
