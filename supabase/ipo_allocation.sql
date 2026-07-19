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
