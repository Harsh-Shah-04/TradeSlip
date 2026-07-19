-- Client Master search indexes.
-- The list endpoints filter with ilike.*term* (leading wildcard), which b-tree
-- indexes cannot serve. pg_trgm GIN indexes make those searches indexed.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_ipo_applicants_name_trgm
  ON ipo_applicants USING gin (name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_ipo_applicants_pan_trgm
  ON ipo_applicants USING gin (pan gin_trgm_ops)
  WHERE pan <> '';

CREATE INDEX IF NOT EXISTS idx_ipo_applicants_dpid_trgm
  ON ipo_applicants USING gin (dpid gin_trgm_ops)
  WHERE dpid <> '';

CREATE INDEX IF NOT EXISTS idx_ipo_parties_name_trgm
  ON ipo_parties USING gin (name gin_trgm_ops);
