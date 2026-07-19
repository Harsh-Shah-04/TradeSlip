-- Client Master performance indexes for applicant lookups/filters.

CREATE INDEX IF NOT EXISTS idx_ipo_applicants_dpid
  ON ipo_applicants (upper(dpid))
  WHERE dpid <> '';

CREATE INDEX IF NOT EXISTS idx_ipo_applicants_party_archived
  ON ipo_applicants (party_id, is_archived);
