# Phase 3 — IPO Allotment & Listing Workflow: Design

Reviewed via `/plan-eng-review` on 2026-07-19. All decisions below were made interactively
and are final for this phase. Implementation target: Cursor agent against this document.

## Problem

The system ends today at Buy → Sell → Applicant Allocation → Confirmation. Once IPO
allotment results are announced, each allocated applicant is either **Allotted N shares**
or **Not Allotted**, entered manually. On listing day, allotted shares are sold in the
pre-open market — usually all at one common price, with occasional per-client overrides.
This module is the foundation for future client holdings, ledger, P/L, and settlement.

## Decisions (locked)

| # | Decision | Choice |
|---|----------|--------|
| D3 | Where allotment lives | **1A** — dedicated `ipo_allotments` table (not columns on allocations) |
| D4 | Listing-day sale storage | **2B** — common `listing_price` on `ipo_master` + per-row override + sold flag. No transaction table this phase (see NOT in scope) |
| D5 | Seeding lifecycle | **3A** — idempotent seed + re-sync with drift badges; never deletes |
| D6 | Tests | **T1A** — pytest added to repo; service-layer tests for every path; CI workflow runs them |
| D7 | Allotment key (supersedes part of D3) | **A** — keyed by allocation `(position_id, applicant_id)` with snapshots (`ipo_id`, `broker_id`, `sub_category`, `cost_per_app`). Fixes dual-quota applications (retail + shareholder in one IPO) and preserves cost basis |
| D8 | Realized price | **A** — `sold_price` frozen at mark-sold time; reports never recompute |
| D9 | Access | **B** — Allotment page is **admin-only** (page and all API routes use `AdminAuth`) |

Outside-voice refinements absorbed (spec completions, not new decisions):
`is_archived` column for orphan archiving; seeding is an explicit **POST** (no writes on
GET); bulk mark-sold groups rows by effective price server-side (see Endpoints); service
warns when acting on an `Upcoming` IPO or before `listing_date`; tests assert generated
PostgREST filter strings (the actual risky layer); a CI workflow runs pytest.

## Schema — `supabase/ipo_allotments.sql`

```sql
-- Phase 3: IPO Allotment & Listing

ALTER TABLE ipo_master
  ADD COLUMN IF NOT EXISTS listing_price NUMERIC(18, 4);

CREATE TABLE IF NOT EXISTS ipo_allotments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ipo_id UUID NOT NULL REFERENCES ipo_master (id),
  -- Allocation-level key. SET NULL (not CASCADE): a deleted position orphans
  -- the allotment row visibly instead of destroying financial history.
  position_id UUID REFERENCES ipo_positions (id) ON DELETE SET NULL,
  applicant_id UUID NOT NULL REFERENCES ipo_applicants (id),
  -- Snapshots taken at seed time (allocations are wipe-and-rewrite; positions
  -- can be deleted — these survive both):
  broker_id UUID NOT NULL REFERENCES brokers (id),
  sub_category TEXT NOT NULL DEFAULT '',
  cost_per_app NUMERIC(18, 4),              -- position.buy_amt / position.buy_app
  -- Allotment result:
  status TEXT NOT NULL DEFAULT 'Pending'
    CHECK (status IN ('Pending', 'Allotted', 'Not Allotted')),
  shares_allotted INTEGER NOT NULL DEFAULT 0 CHECK (shares_allotted >= 0),
  -- Listing-day sale (decision 2B + D8):
  listing_price_override NUMERIC(18, 4),
  is_sold BOOLEAN NOT NULL DEFAULT FALSE,
  sold_price NUMERIC(18, 4),                -- frozen at mark-sold; never recomputed
  sold_at DATE,
  -- Lifecycle:
  is_archived BOOLEAN NOT NULL DEFAULT FALSE,
  notes TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- Integrity lives in the schema, not just the service:
  CHECK (status = 'Allotted' OR shares_allotted = 0),
  CHECK (status = 'Allotted' OR is_sold = FALSE),
  CHECK (NOT is_sold OR sold_price IS NOT NULL),
  CHECK (NOT is_sold OR shares_allotted > 0)
);

-- One allotment row per live allocation. Partial: orphaned rows (position_id
-- NULL after a position delete) keep history without blocking a re-seed.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ipo_allotments_position_applicant
  ON ipo_allotments (position_id, applicant_id)
  WHERE position_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ipo_allotments_ipo_status
  ON ipo_allotments (ipo_id, status);
CREATE INDEX IF NOT EXISTS idx_ipo_allotments_applicant
  ON ipo_allotments (applicant_id);
CREATE INDEX IF NOT EXISTS idx_ipo_allotments_broker
  ON ipo_allotments (broker_id);
```

## State machine

```
                       seed (POST /seed, idempotent upsert)
  allocation row ────────────────────────────────► [Pending]
                                                     │    │
                                    mark Allotted    │    │  mark Not Allotted
                                    (shares > 0)     │    │  (shares forced 0)
                                                     ▼    ▼
                                              [Allotted]  [Not Allotted]
                                                  │  ▲            │
                          mark sold (price req.)  │  │ unmark     │ (terminal unless
                          freezes sold_price      ▼  │ sold       │  status corrected)
                                              [Allotted+SOLD]     ▼
                                                                (end)

  Any state ──(source allocation removed / position deleted)──► ORPHANED badge
                                        └─ one-click archive (is_archived = TRUE)
  Status can be corrected at any time BEFORE is_sold = TRUE; after that,
  unmark-sold first (clears sold_price/sold_at), then correct.
```

## Data flow

```
 ipo_position_allocations ─┐
                           │  POST /api/ipo/allotments/seed {ipo_id}
 ipo_positions (ipo_id,    ├─────────────────────────────────────────┐
   broker_id, buy_amt,     │   1 read (allocations + embedded        │
   buy_app, sub_category) ─┘     positions, single PostgREST call)   ▼
                                                        upsert missing rows as Pending
                                                        (on_conflict=position_id,applicant_id,
                                                         resolution=ignore-duplicates,
                                                         chunks of 100)
                                                                      │
 GET /api/ipo/allotments?ipo_id=X  ◄──────────────────────────────────┘
   one call: ipo_allotments?ipo_id=eq.X
     &select=*,ipo_applicants(name,pan,ipo_parties(name))
   + drift report (set-diff vs live allocations: missing → seed hint,
     orphaned → badge)
                                                                      │
 Admin enters results (PATCH per row: status, shares, override, notes)│
                                                                      ▼
 Listing day:  PATCH /api/ipo/ipos/{id} {listing_price: 106.5}   (existing route, new field)
               POST /api/ipo/allotments/mark-sold {ipo_id, sell_date}
                 service: fetch Allotted+unsold rows → group by effective
                 price COALESCE(override, common) → reject group with NULL
                 price → one PATCH per price group (id=in.(...)) setting
                 is_sold, sold_price, sold_at
```

## Service layer — `utils/ipo/allotments.py`

Mirrors `utils/ipo/clients.py` conventions exactly: shared `_http()` client,
`_service_headers()`, chunked bulk writes (100/batch), Pydantic models in
`utils/ipo/models.py` (`AllotmentUpdate`, `MarkSoldRequest`, `allotment_to_json`).

Functions and their guards:

- `seed_allotments(ipo_id)` — one read of allocations (with embedded position fields),
  compute snapshots (`cost_per_app = buy_amt / buy_app`, guard `buy_app > 0`), bulk
  upsert Pending rows. Idempotent. Warn (not block) if IPO status is `Upcoming`.
- `drift_report(ipo_id)` — set-diff of live allocation pairs vs allotment rows:
  `missing` (allocated, no row → UI offers re-seed) and `orphaned` (row's position_id
  NULL or pair no longer allocated → UI badge + archive action). Never deletes.
- `update_allotment(allotment_id, payload)` — guards: `Allotted` requires
  `shares_allotted > 0`; `Not Allotted`/`Pending` forces shares to 0; reject any edit of
  status/shares on a sold row (unmark first); reject unknown status.
- `mark_sold(ipo_id, sell_date)` (bulk) and `mark_sold_row(allotment_id, sell_date)` —
  effective price = `COALESCE(listing_price_override, ipo_master.listing_price)`;
  reject rows whose effective price is NULL with a clear message; write `sold_price`,
  `sold_at`, `is_sold` in one PATCH per distinct price value. Warn if
  `listing_date` is unset or in the future.
- `unmark_sold(allotment_id)` — clears `is_sold`, `sold_price`, `sold_at` (mistake path).
- `archive_allotment(allotment_id)` — orphan cleanup, sets `is_archived`.

## Endpoints (all `AdminAuth` per D9-B)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/ipo/allotments` | Page (Jinja template, admin-gated) |
| GET | `/api/ipo/allotments?ipo_id=` | List rows + drift report (read-only, no side effects) |
| POST | `/api/ipo/allotments/seed` | Seed/re-sync rows for an IPO (explicit, never on GET) |
| PATCH | `/api/ipo/allotments/{id}` | Update status / shares / override / notes |
| POST | `/api/ipo/allotments/{id}/archive` | Archive an orphaned row |
| POST | `/api/ipo/allotments/mark-sold` | Bulk sell `{ipo_id, sell_date}` |
| POST | `/api/ipo/allotments/{id}/mark-sold` | Sell one row |
| POST | `/api/ipo/allotments/{id}/unmark-sold` | Undo a mistaken sale |

Plus: add `listing_price` to `IpoMasterUpdate` / `update_ipo` (existing route carries it).

## UI — `templates/ipo_allotments.html`

Follows `ipo_clients.html` patterns (vanilla JS, `TradeSlip.apiFetch`, escapeHtml).
IPO selector → table: Party | Applicant | PAN | Sub-Category | Broker | Cost/App |
Status (Pending/Allotted/Not Allotted) | Shares | Override Price | Sold (badge + price).
Header bar: common listing price input + "Apply & Mark Sold" button (confirm dialog
showing N rows and the price), seed/re-sync button with drift badges, summary chips
(x Pending / y Allotted / z Not Allotted / w Sold). Orphaned rows get an amber badge
with an Archive action.

**Navigation (locked):** Keep existing tabs unchanged (Positions, Confirmations,
Client Master, IPO Master). Add Phase 3 as new top-level items only:

Positions | Confirmations | **Allotments** | **Settlement & Reports** |
**Client Ledger** | Client Master | IPO Master

Do not merge Phase 3 into existing pages.

## Tests — `tests/test_allotments.py` (pytest, new)

`pytest` added to `requirements.txt`; `.github/workflows/tests.yml` runs it on push/PR.
HTTP layer mocked at the `_http()` boundary **and** tests assert the generated PostgREST
URL/params/filter strings (per outside voice: filter strings are the risky layer).

Required cases (from the coverage diagram — all 17 paths):
seed first-open inserts / re-open zero inserts / late-allocation adds row;
drift orphan detection / no-drift empty; status guards (Allotted needs shares>0,
Not Allotted forces 0, unknown rejected, sold rows locked); effective-price resolution
(override wins, both NULL → error not 0); bulk mark-sold skips Not-Allotted and
already-sold, groups by price, NULL-price group rejected with message; unmark-sold
clears all three sale fields; cost_per_app snapshot math incl. buy_app=0 guard;
double-submit idempotency (seed and mark-sold); Supabase 4xx/5xx surfaces a message.

## Failure modes (per new codepath)

| Codepath | Realistic failure | Test | Handling | User sees |
|----------|-------------------|------|----------|-----------|
| seed | allocation edited mid-seed (race) | ✓ idempotency test | upsert ignore-duplicates | re-sync badge |
| seed | buy_app = 0 division | ✓ | guard, cost NULL + warning | warning chip |
| mark-sold bulk | common price NULL, some overrides | ✓ | per-group reject with names | clear error listing rows |
| mark-sold | double-click resubmit | ✓ | filter `is_sold=eq.false` makes 2nd call a no-op | no duplicate state |
| PATCH row | stale row (sold by other tab) | ✓ | sold-row edits rejected | error message |
| any | Supabase 5xx | ✓ | RuntimeError → 502 JSON | error banner |

No critical gaps: every new path has a planned test AND error handling AND a visible error.

## What already exists (reused, not rebuilt)

- Eligible-applicant set: `ipo_position_allocations` × `ipo_positions` — consumed by seed.
- `ipo_master.listing_date` — used for lifecycle warnings; `listing_price` joins it.
- `ipo_sell_parties` — not needed this phase (no counterparty on listing sales per 2B).
- `ipo_sells` — deliberately NOT reused: application-unit grey-market sells; listing
  sales are share-unit. Different asset, different lifecycle.
- Service/HTTP/bulk-write/pagination patterns from `utils/ipo/clients.py`.
- Auth: existing `AdminAuth` dependency; session cache from the perf fix applies.

## NOT in scope (considered, deferred)

- **Transaction-grade listing trade records** — deferred by decision 2B; captured in
  TODOS.md with migration path (lossless thanks to frozen `sold_price`).
- **Broker read-only allotment view** — deferred by D9-B; captured in TODOS.md.
- **Holdings/ledger/P&L/settlement modules** — future phases; this schema feeds them.
- **Allotment file import (registrar Excel/CSV)** — manual entry per the stated workflow;
  revisit if volume makes manual entry painful.
- **Partial share sales / multiple sale batches** — cannot be represented under 2B;
  arrives with the transaction table in the ledger phase.

## Implementation Tasks

Synthesized from this review's findings. Run with Cursor/Claude Code; checkbox as you ship.

- [ ] **T1 (P1, human: ~1h / CC: ~5min)** — schema — Create `supabase/ipo_allotments.sql` exactly as specified; run in Supabase SQL editor
  - Surfaced by: Architecture 1A + D7 + D8 (key, snapshots, frozen price, CHECKs)
  - Files: `supabase/ipo_allotments.sql`
  - Verify: table + constraints visible in Supabase; inserting `status='Allotted', shares=0` fails
- [ ] **T2 (P1, human: ~4h / CC: ~20min)** — service — `utils/ipo/allotments.py` with seed/drift/update/mark-sold/unmark/archive + guards
  - Surfaced by: Architecture 3A, Code Quality guards, outside voice #7/#8/#10
  - Files: `utils/ipo/allotments.py`, `utils/ipo/models.py`
  - Verify: `pytest tests/test_allotments.py`
- [ ] **T3 (P1, human: ~2h / CC: ~10min)** — routes — 8 admin endpoints + `listing_price` on `IpoMasterUpdate`
  - Surfaced by: D9-B access decision
  - Files: `main.py`
  - Verify: non-admin gets 403 on every route; admin flow works end-to-end
- [ ] **T4 (P1, human: ~4h / CC: ~20min)** — tests — pytest infra + all 17 diagrammed paths incl. filter-string assertions
  - Surfaced by: Test review T1A + outside voice #9
  - Files: `requirements.txt`, `tests/test_allotments.py`, `.github/workflows/tests.yml`
  - Verify: CI green on the PR
- [ ] **T5 (P2, human: ~4h / CC: ~20min)** — UI — `templates/ipo_allotments.html` + nav link
  - Surfaced by: plan scope (entry + listing-day screens)
  - Files: `templates/ipo_allotments.html`, `templates/base.html`
  - Verify: manual QA against the test-plan artifact
- [ ] **T6 (P2, human: ~1h / CC: ~5min)** — docs — keep this design doc's diagrams accurate as implementation lands; embed the state machine as a comment atop `allotments.py`
  - Surfaced by: documentation/diagram preference
  - Files: `utils/ipo/allotments.py`
  - Verify: diagram matches shipped guards

## Worktree parallelization

| Step | Modules touched | Depends on |
|------|-----------------|------------|
| T1 schema | supabase/ | — |
| T2 service + T4 tests | utils/ipo/, tests/, requirements | T1 |
| T3 routes | main.py | T2 |
| T5 UI | templates/ | T3 (API shapes) |
| T6 docs | utils/ipo/ | T2 |

Lane A: T1 → T2+T4 → T3 (sequential, shared service surface).
Lane B: T5 template skeleton can start in parallel after T1 (mock API responses), final
wiring waits for T3. Conflict flag: none — lanes touch disjoint directories until T3/T5
integration.

Effectively: mostly sequential; parallelism is limited to UI skeleton work. A single
Cursor session running T1→T6 in order is the simplest correct path.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR (PLAN) | 5 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **CROSS-MODEL:** Outside voice (Claude subagent; Codex not installed) raised 10 findings; 3 became user decisions (D7 accepted, D8 accepted, D9 resolved admin-only), 5 absorbed as spec completions, 2 were restatements of settled decisions.
- **VERDICT:** ENG CLEARED — ready to implement.

NO UNRESOLVED DECISIONS
