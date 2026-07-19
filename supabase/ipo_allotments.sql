-- Phase 3: IPO Allotment & Listing Day sales

ALTER TABLE ipo_master
  ADD COLUMN IF NOT EXISTS listing_price NUMERIC(18, 4);

COMMENT ON COLUMN ipo_master.listing_price IS
  'Common listing-day sell premium; per-applicant overrides live on ipo_allotments';

CREATE TABLE IF NOT EXISTS ipo_allotments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ipo_id UUID NOT NULL REFERENCES ipo_master (id),
  position_id UUID REFERENCES ipo_positions (id) ON DELETE SET NULL,
  applicant_id UUID NOT NULL REFERENCES ipo_applicants (id),
  broker_id UUID NOT NULL REFERENCES brokers (id),
  sub_category TEXT NOT NULL DEFAULT '',
  cost_per_app NUMERIC(18, 4),
  status TEXT NOT NULL DEFAULT 'Pending'
    CHECK (status IN ('Pending', 'Allotted', 'Not Allotted')),
  shares_allotted INTEGER NOT NULL DEFAULT 0 CHECK (shares_allotted >= 0),
  listing_price_override NUMERIC(18, 4),
  is_sold BOOLEAN NOT NULL DEFAULT FALSE,
  sold_price NUMERIC(18, 4),
  sold_at DATE,
  is_archived BOOLEAN NOT NULL DEFAULT FALSE,
  notes TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (status = 'Allotted' OR shares_allotted = 0),
  CHECK (status = 'Allotted' OR is_sold = FALSE),
  CHECK (NOT is_sold OR sold_price IS NOT NULL),
  CHECK (NOT is_sold OR shares_allotted > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ipo_allotments_position_applicant
  ON ipo_allotments (position_id, applicant_id)
  WHERE position_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ipo_allotments_ipo_status
  ON ipo_allotments (ipo_id, status);
CREATE INDEX IF NOT EXISTS idx_ipo_allotments_applicant
  ON ipo_allotments (applicant_id);
CREATE INDEX IF NOT EXISTS idx_ipo_allotments_broker
  ON ipo_allotments (broker_id);
