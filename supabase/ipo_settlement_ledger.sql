-- Phase 3: Settlement runs + continuous Client Ledger

CREATE TABLE IF NOT EXISTS ipo_settlements (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ipo_id UUID NOT NULL REFERENCES ipo_master (id),
  broker_id UUID NOT NULL REFERENCES brokers (id),
  status TEXT NOT NULL DEFAULT 'Draft'
    CHECK (status IN ('Draft', 'Finalized')),
  listing_price_used NUMERIC(18, 4),
  notes TEXT NOT NULL DEFAULT '',
  finalized_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ipo_settlements_one_finalized_per_ipo
  ON ipo_settlements (ipo_id)
  WHERE status = 'Finalized';

CREATE INDEX IF NOT EXISTS idx_ipo_settlements_ipo
  ON ipo_settlements (ipo_id);

CREATE TABLE IF NOT EXISTS ipo_settlement_lines (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  settlement_id UUID NOT NULL REFERENCES ipo_settlements (id) ON DELETE CASCADE,
  allotment_id UUID REFERENCES ipo_allotments (id) ON DELETE SET NULL,
  party_id UUID REFERENCES ipo_parties (id) ON DELETE SET NULL,
  applicant_id UUID REFERENCES ipo_applicants (id) ON DELETE SET NULL,
  position_id UUID REFERENCES ipo_positions (id) ON DELETE SET NULL,
  party_name TEXT NOT NULL DEFAULT '',
  applicant_name TEXT NOT NULL DEFAULT '',
  pan TEXT NOT NULL DEFAULT '',
  dpid TEXT NOT NULL DEFAULT '',
  sub_category TEXT NOT NULL DEFAULT '',
  application_amount NUMERIC(18, 4),
  vyaj NUMERIC(18, 4) NOT NULL DEFAULT 0,
  applied NUMERIC(18, 4) NOT NULL DEFAULT 1,
  allotted_apps NUMERIC(18, 4) NOT NULL DEFAULT 0,
  shares_allotted INTEGER NOT NULL DEFAULT 0,
  sell_premium NUMERIC(18, 4) NOT NULL DEFAULT 0,
  sell_amt NUMERIC(18, 4) NOT NULL DEFAULT 0,
  net_pl NUMERIC(18, 4) NOT NULL DEFAULT 0,
  direction TEXT NOT NULL DEFAULT 'Settled'
    CHECK (direction IN ('Receivable', 'Payable', 'Settled')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ipo_settlement_lines_settlement
  ON ipo_settlement_lines (settlement_id);
CREATE INDEX IF NOT EXISTS idx_ipo_settlement_lines_party
  ON ipo_settlement_lines (party_id);

CREATE TABLE IF NOT EXISTS ipo_ledger_entries (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  party_id UUID NOT NULL REFERENCES ipo_parties (id),
  ipo_id UUID REFERENCES ipo_master (id),
  entry_type TEXT NOT NULL
    CHECK (entry_type IN ('Settlement', 'PaymentReceived', 'PaymentPaid', 'Adjustment')),
  -- + receivable (client owes firm), - payable (firm owes client)
  amount NUMERIC(18, 4) NOT NULL,
  balance_after NUMERIC(18, 4) NOT NULL,
  reference_type TEXT NOT NULL DEFAULT '',
  reference_id UUID,
  entry_date DATE NOT NULL DEFAULT CURRENT_DATE,
  notes TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ipo_ledger_party_created
  ON ipo_ledger_entries (party_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_ipo_ledger_ipo
  ON ipo_ledger_entries (ipo_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ipo_ledger_settlement_party_once
  ON ipo_ledger_entries (party_id, reference_id)
  WHERE entry_type = 'Settlement' AND reference_type = 'settlement' AND reference_id IS NOT NULL;
