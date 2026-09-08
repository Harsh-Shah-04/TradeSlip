BEGIN;

ALTER TABLE ipo_positions
  ADD COLUMN IF NOT EXISTS trade_direction TEXT NOT NULL DEFAULT 'client_to_counterparty';

ALTER TABLE ipo_positions
  ADD COLUMN IF NOT EXISTS counterparty_sell_party TEXT NOT NULL DEFAULT '';

ALTER TABLE ipo_positions
  ADD COLUMN IF NOT EXISTS counterparty_sell_party_id UUID NULL REFERENCES ipo_sell_parties (id);

UPDATE ipo_positions
SET
  trade_direction = COALESCE(NULLIF(trade_direction, ''), 'client_to_counterparty'),
  counterparty_sell_party = COALESCE(counterparty_sell_party, '')
WHERE trade_direction IS NULL
   OR trade_direction = ''
   OR counterparty_sell_party IS NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'ipo_positions_trade_direction_check'
  ) THEN
    ALTER TABLE ipo_positions
      ADD CONSTRAINT ipo_positions_trade_direction_check
      CHECK (trade_direction IN ('client_to_counterparty', 'counterparty_to_client'));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_ipo_positions_counterparty_sell_party_id
  ON ipo_positions (counterparty_sell_party_id)
  WHERE counterparty_sell_party_id IS NOT NULL;

COMMIT;
