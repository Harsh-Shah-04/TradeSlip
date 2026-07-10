-- Trade Slip Dashboard: run once in the Supabase SQL editor.
--
-- 1. Execute this script to create the daily_trade_slips table.
-- 2. In Storage, create a PRIVATE bucket named: trade-slips
--    (Dashboard → Storage → New bucket → Name: trade-slips → Public bucket: OFF)
-- 3. Upload your blank slip PDF to:
--      trade-slips/templates/blank-trade-slip.pdf
--    (required for Vercel / any host that does not have assets/ locally)
-- 4. Auth (required):
--    - Authentication → Users → Add user (email + password for the broker)
--    - Disable public sign-ups so only invited users exist
--    - Set ALLOWED_EMAIL in the app env to that user's email
-- 5. Keep the service-role key server-side only (never in the browser or Git).

CREATE TABLE IF NOT EXISTS daily_trade_slips (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_code TEXT NOT NULL,
  client_name TEXT NOT NULL,
  trade_date DATE NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('Unsigned', 'Signed')),
  public_url TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (client_code, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_daily_trade_slips_trade_date ON daily_trade_slips (trade_date);
CREATE INDEX IF NOT EXISTS idx_daily_trade_slips_status ON daily_trade_slips (status);
