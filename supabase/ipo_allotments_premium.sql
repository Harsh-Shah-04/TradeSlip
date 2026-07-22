-- Premium listing settlement — allow one allotment row per Premium position
-- (no applicant). Shares are already fixed; the row only records listing
-- sold price / date for Subject-2-style difference settlement.
--
-- Safe to re-run.

ALTER TABLE ipo_allotments
  ALTER COLUMN applicant_id DROP NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_ipo_allotments_premium_position
  ON ipo_allotments (position_id)
  WHERE applicant_id IS NULL AND position_id IS NOT NULL;
