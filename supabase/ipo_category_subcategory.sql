-- Category / Sub-Category split for IPO positions.
-- Existing rows: category (e.g. 15K) → sub_category; category → 'IPO Application'.

ALTER TABLE ipo_positions
  ADD COLUMN IF NOT EXISTS sub_category TEXT NOT NULL DEFAULT '';

-- Migrate legacy single Category field into Sub-Category, then set main Category.
UPDATE ipo_positions
SET
  sub_category = CASE
    WHEN COALESCE(sub_category, '') <> '' THEN sub_category
    WHEN upper(trim(category)) IN ('2-SHARE', '2- SHARE', '2-') THEN '2-'
    WHEN upper(trim(category)) IN ('15K (SHAREHOLDER)', '15K SHAREHOLDER') THEN '15K Shareholder'
    WHEN upper(trim(category)) IN ('2- SHAREHOLDER', '2-SHAREHOLDER', 'SHARE') THEN '2- Shareholder'
    ELSE trim(category)
  END,
  category = CASE
    WHEN category IN ('IPO Application', 'Premium', 'Subject 2') THEN category
    ELSE 'IPO Application'
  END
WHERE COALESCE(sub_category, '') = ''
   OR category NOT IN ('IPO Application', 'Premium', 'Subject 2');

-- Second pass: any remaining non-main category values still look like sub-codes
UPDATE ipo_positions
SET
  sub_category = trim(category),
  category = 'IPO Application'
WHERE category NOT IN ('IPO Application', 'Premium', 'Subject 2');

CREATE INDEX IF NOT EXISTS idx_ipo_positions_trade_category
  ON ipo_positions (category);

CREATE INDEX IF NOT EXISTS idx_ipo_positions_sub_category
  ON ipo_positions (sub_category);
