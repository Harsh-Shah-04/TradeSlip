-- Rename legacy optional dalal amount to earned brokerage on grey-market sells.
-- Safe to run multiple times.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'ipo_sells' AND column_name = 'dalal'
  ) AND NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'ipo_sells' AND column_name = 'brokerage'
  ) THEN
    ALTER TABLE ipo_sells RENAME COLUMN dalal TO brokerage;
  ELSIF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'ipo_sells' AND column_name = 'brokerage'
  ) THEN
    ALTER TABLE ipo_sells ADD COLUMN brokerage NUMERIC(18, 4);
  END IF;
END $$;

COMMENT ON COLUMN ipo_sells.brokerage IS
  'Earned brokerage = sell_amt − (sell_app × buy_rate); calculated by backend';
