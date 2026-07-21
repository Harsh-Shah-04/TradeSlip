-- Client Ledger v2 — sell-party accounts + open-item (IPO-wise) settlement.
--
-- Two changes:
--   1. A ledger entry can now belong to a SELL party (Ambica, Mama, …) instead of
--      a buy party. Exactly one of party_id / sell_party_id is set.
--   2. Payments allocate against specific charges (one charge = one IPO settlement),
--      so every charge has a real Pending / Part paid / Done state.
--
-- Safe to re-run.

-- ---------------------------------------------------------------- 1. sell parties

ALTER TABLE ipo_ledger_entries ALTER COLUMN party_id DROP NOT NULL;

ALTER TABLE ipo_ledger_entries
  ADD COLUMN IF NOT EXISTS sell_party_id UUID REFERENCES ipo_sell_parties (id);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'ipo_ledger_entries_one_account'
  ) THEN
    ALTER TABLE ipo_ledger_entries
      ADD CONSTRAINT ipo_ledger_entries_one_account
      CHECK ((party_id IS NOT NULL) <> (sell_party_id IS NOT NULL));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_ipo_ledger_sell_party_created
  ON ipo_ledger_entries (sell_party_id, created_at ASC)
  WHERE sell_party_id IS NOT NULL;

-- The existing unique index only covers party_id; sell parties need their own.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ipo_ledger_settlement_sell_party_once
  ON ipo_ledger_entries (sell_party_id, reference_id)
  WHERE entry_type = 'Settlement'
    AND reference_type = 'settlement'
    AND sell_party_id IS NOT NULL;

-- ---------------------------------------------------------------- 2. allocations

-- One row = "this payment cleared this much of this charge".
-- amount is always a POSITIVE magnitude; direction comes from the charge.
CREATE TABLE IF NOT EXISTS ipo_ledger_allocations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  payment_id UUID NOT NULL REFERENCES ipo_ledger_entries (id) ON DELETE CASCADE,
  charge_id  UUID NOT NULL REFERENCES ipo_ledger_entries (id) ON DELETE CASCADE,
  amount NUMERIC(18, 4) NOT NULL CHECK (amount > 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ipo_ledger_alloc_charge
  ON ipo_ledger_allocations (charge_id);
CREATE INDEX IF NOT EXISTS idx_ipo_ledger_alloc_payment
  ON ipo_ledger_allocations (payment_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ipo_ledger_alloc_pair
  ON ipo_ledger_allocations (payment_id, charge_id);
