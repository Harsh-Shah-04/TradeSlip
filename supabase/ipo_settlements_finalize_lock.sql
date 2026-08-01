-- Serialize Finalize / Post revision so a double-click cannot race ledger Adjustments.
ALTER TABLE ipo_settlements
  ADD COLUMN IF NOT EXISTS finalize_lock_until TIMESTAMPTZ;

COMMENT ON COLUMN ipo_settlements.finalize_lock_until IS
  'When set in the future, another Finalize/revision must wait — prevents double-submit races.';
