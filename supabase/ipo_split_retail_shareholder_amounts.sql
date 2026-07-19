-- Split Retail / Shareholder application amounts into independent 15K and 2- fields.
-- Legacy shared columns (amount_retail, amount_shareholder) are backfilled then dropped.

ALTER TABLE ipo_master
  ADD COLUMN IF NOT EXISTS amount_retail_15k NUMERIC(18, 4),
  ADD COLUMN IF NOT EXISTS amount_retail_2minus NUMERIC(18, 4),
  ADD COLUMN IF NOT EXISTS amount_shareholder_15k NUMERIC(18, 4),
  ADD COLUMN IF NOT EXISTS amount_shareholder_2minus NUMERIC(18, 4);

UPDATE ipo_master
SET
  amount_retail_15k = COALESCE(amount_retail_15k, amount_retail),
  amount_retail_2minus = COALESCE(amount_retail_2minus, amount_retail),
  amount_shareholder_15k = COALESCE(amount_shareholder_15k, amount_shareholder),
  amount_shareholder_2minus = COALESCE(amount_shareholder_2minus, amount_shareholder)
WHERE amount_retail IS NOT NULL
   OR amount_shareholder IS NOT NULL;

COMMENT ON COLUMN ipo_master.amount_retail_15k IS 'Retail 15K application amount';
COMMENT ON COLUMN ipo_master.amount_retail_2minus IS 'Retail 2- application amount';
COMMENT ON COLUMN ipo_master.amount_shareholder_15k IS '15K Shareholder application amount';
COMMENT ON COLUMN ipo_master.amount_shareholder_2minus IS '2- Shareholder application amount';

ALTER TABLE ipo_master
  DROP COLUMN IF EXISTS amount_retail,
  DROP COLUMN IF EXISTS amount_shareholder;
