# TODOS

## Phase 4+ — Migrate listing sales to transaction-grade records

- **What:** When the ledger phase starts, derive/migrate listing-day sales from `ipo_allotments` (`sold_price`, `sold_at`, `shares_allotted`) into per-sale transaction rows (an `ipo_listing_trades`-style table).
- **Why:** Client ledger, P/L, and settlement reports consume transactions (what was sold, when, at what price, to whom). Phase 3 deliberately stores sales as columns on the allotment row (decision 2B in `docs/phase3-allotment-listing-design.md`), which cannot represent multiple sale batches or partial sales.
- **Pros:** Lossless migration is guaranteed because `sold_price` is frozen at mark-sold time (decision D8) — every sold row carries its own permanent price.
- **Cons:** None now; it is a documented deferral. Cost lands in the ledger phase as one migration script.
- **Context:** See "NOT in scope" in the Phase 3 design doc. The allotment row is the single source for realized listing-day proceeds until this migrates.
- **Depends on / blocked by:** Phase 3 shipped and in use.

## Broker read-only access to allotment results

- **What:** A read-only allotment view filtered to `broker_id = current broker`, so brokers can self-serve their clients' results.
- **Why:** Phase 3 makes the Allotment page admin-only (decision D9-B); brokers must ask the admin for results. If that becomes a bottleneck, this is the fix.
- **Pros:** Cheap — `broker_id` is snapshotted on every allotment row (decision D7), so this is one route + a filtered query + template reuse, not a redesign.
- **Cons:** One more page to maintain; may never be needed if admin-only works fine in practice.
- **Context:** Decisions D7 and D9 in `docs/phase3-allotment-listing-design.md`.
- **Depends on / blocked by:** Phase 3 shipped; only build when a broker actually asks.
