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
