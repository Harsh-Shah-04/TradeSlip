-- IPO Master — default application amounts by category bucket.
-- Configure once per IPO; buy forms auto-fill Application Amount from Sub-Category.

ALTER TABLE ipo_master
  ADD COLUMN IF NOT EXISTS amount_bhni NUMERIC(18, 4),
  ADD COLUMN IF NOT EXISTS amount_shni NUMERIC(18, 4),
  ADD COLUMN IF NOT EXISTS amount_retail NUMERIC(18, 4),
  ADD COLUMN IF NOT EXISTS amount_shareholder NUMERIC(18, 4);

COMMENT ON COLUMN ipo_master.amount_bhni IS 'bHNI (10+) application amount';
COMMENT ON COLUMN ipo_master.amount_shni IS 'sHNI (2+) application amount';
COMMENT ON COLUMN ipo_master.amount_retail IS 'Retail amount for 15K and 2-';
COMMENT ON COLUMN ipo_master.amount_shareholder IS 'Shareholder amount for 15K Shareholder and 2- Shareholder';
