-- IPO Master — default application amounts by Sub-Category.
-- Configure once per IPO; buy forms auto-fill Application Amount from Sub-Category.

ALTER TABLE ipo_master
  ADD COLUMN IF NOT EXISTS amount_bhni NUMERIC(18, 4),
  ADD COLUMN IF NOT EXISTS amount_shni NUMERIC(18, 4),
  ADD COLUMN IF NOT EXISTS amount_retail_15k NUMERIC(18, 4),
  ADD COLUMN IF NOT EXISTS amount_retail_2minus NUMERIC(18, 4),
  ADD COLUMN IF NOT EXISTS amount_shareholder_15k NUMERIC(18, 4),
  ADD COLUMN IF NOT EXISTS amount_shareholder_2minus NUMERIC(18, 4);

COMMENT ON COLUMN ipo_master.amount_bhni IS 'bHNI (10+) application amount';
COMMENT ON COLUMN ipo_master.amount_shni IS 'sHNI (2+) application amount';
COMMENT ON COLUMN ipo_master.amount_retail_15k IS 'Retail 15K application amount';
COMMENT ON COLUMN ipo_master.amount_retail_2minus IS 'Retail 2- application amount';
COMMENT ON COLUMN ipo_master.amount_shareholder_15k IS '15K Shareholder application amount';
COMMENT ON COLUMN ipo_master.amount_shareholder_2minus IS '2- Shareholder application amount';
