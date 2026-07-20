"""Phase 3 — IPO Allotment & Listing Day sales.

State machine:
  seed → Pending → Allotted (shares>0) | Not Allotted (shares=0)
  Allotted → mark-sold freezes sold_price; unmark clears sale fields.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import httpx

from utils.ipo.models import AllotmentUpdate, allotment_to_json
from utils.ipo.service import get_ipo
from utils.supabase_client import _service_headers, _supabase_url

ALLOTMENTS_TABLE = "ipo_allotments"
ALLOCATIONS_TABLE = "ipo_position_allocations"
POSITIONS_TABLE = "ipo_positions"
HTTP_TIMEOUT = 30.0
MONEY_QUANT = Decimal("0.0001")
CHUNK = 100

_http_client: httpx.Client | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _http() -> httpx.Client:
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(timeout=HTTP_TIMEOUT)
    return _http_client


def _money(value: float | int | Decimal | str | None) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _pair_key(position_id: str | None, applicant_id: str) -> str:
    return f"{position_id or ''}::{applicant_id}"


def _live_allocation_pairs(ipo_id: str) -> list[dict[str, Any]]:
    """Return allocation rows for fully-allocated non-premium positions of this IPO."""
    pos_resp = _http().get(
        f"{_supabase_url()}/rest/v1/{POSITIONS_TABLE}",
        headers=_service_headers(),
        params={
            "select": "id,ipo_id,broker_id,category,sub_category,buy_app,buy_amt,buy_rate,party_id",
            "ipo_id": f"eq.{ipo_id}",
            "limit": "5000",
        },
        timeout=HTTP_TIMEOUT,
    )
    if pos_resp.status_code != 200:
        raise RuntimeError(f"Load positions failed ({pos_resp.status_code}): {pos_resp.text[:300]}")
    positions = pos_resp.json() if isinstance(pos_resp.json(), list) else []
    eligible = [
        p
        for p in positions
        if (p.get("category") or "") != "Premium" and float(p.get("buy_app") or 0) > 0
    ]
    if not eligible:
        return []

    by_id = {str(p["id"]): p for p in eligible}
    ids = list(by_id.keys())
    pairs: list[dict[str, Any]] = []
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i : i + CHUNK]
        id_filter = "(" + ",".join(chunk) + ")"
        alloc_resp = _http().get(
            f"{_supabase_url()}/rest/v1/{ALLOCATIONS_TABLE}",
            headers=_service_headers(),
            params={
                "select": "position_id,applicant_id",
                "position_id": f"in.{id_filter}",
                "limit": "5000",
            },
            timeout=HTTP_TIMEOUT,
        )
        if alloc_resp.status_code != 200:
            raise RuntimeError(
                f"Load allocations failed ({alloc_resp.status_code}): {alloc_resp.text[:300]}"
            )
        rows = alloc_resp.json() if isinstance(alloc_resp.json(), list) else []
        for row in rows:
            pid = str(row.get("position_id") or "")
            aid = str(row.get("applicant_id") or "")
            pos = by_id.get(pid)
            if not pos or not aid:
                continue
            buy_app = float(pos.get("buy_app") or 0)
            buy_amt = float(pos.get("buy_amt") or 0)
            cost = None
            if buy_app > 0:
                cost = float(_money(buy_amt) / _money(buy_app))
            pairs.append(
                {
                    "position_id": pid,
                    "applicant_id": aid,
                    "ipo_id": str(pos.get("ipo_id") or ipo_id),
                    "broker_id": str(pos.get("broker_id") or ""),
                    "sub_category": pos.get("sub_category") or "",
                    "cost_per_app": cost,
                    "buy_app": buy_app,
                }
            )

    # Only seed positions that are fully allocated (count == round(buy_app))
    counts: dict[str, int] = defaultdict(int)
    for p in pairs:
        counts[p["position_id"]] += 1
    filtered: list[dict[str, Any]] = []
    for p in pairs:
        required = int(round(float(by_id[p["position_id"]].get("buy_app") or 0)))
        if counts[p["position_id"]] == required and required > 0:
            filtered.append(p)
    return filtered


def list_allotments_raw(ipo_id: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
    params: dict[str, str] = {
        "select": (
            "*,ipo_applicants(id,name,pan,dpid,party_id,ipo_parties(id,name))"
        ),
        "ipo_id": f"eq.{ipo_id}",
        "order": "created_at.asc",
        "limit": "10000",
    }
    if not include_archived:
        params["is_archived"] = "eq.false"
    response = _http().get(
        f"{_supabase_url()}/rest/v1/{ALLOTMENTS_TABLE}",
        headers=_service_headers(),
        params=params,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"List allotments failed ({response.status_code}): {response.text[:300]}")
    rows = response.json()
    return rows if isinstance(rows, list) else []


def get_allotment(allotment_id: str) -> dict[str, Any]:
    response = _http().get(
        f"{_supabase_url()}/rest/v1/{ALLOTMENTS_TABLE}",
        headers=_service_headers(),
        params={
            "id": f"eq.{allotment_id}",
            "select": "*,ipo_applicants(id,name,pan,dpid,party_id,ipo_parties(id,name))",
            "limit": "1",
        },
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Get allotment failed ({response.status_code}): {response.text[:300]}")
    rows = response.json()
    if not isinstance(rows, list) or not rows:
        raise LookupError("Allotment not found.")
    return rows[0]


def drift_report(ipo_id: str) -> dict[str, Any]:
    live = _live_allocation_pairs(ipo_id)
    live_keys = {_pair_key(p["position_id"], p["applicant_id"]) for p in live}
    allotments = list_allotments_raw(ipo_id, include_archived=False)
    allot_keys: set[str] = set()
    orphaned: list[dict[str, Any]] = []
    for row in allotments:
        pid = row.get("position_id")
        aid = str(row.get("applicant_id") or "")
        key = _pair_key(str(pid) if pid else None, aid)
        if pid is None or key not in live_keys:
            orphaned.append(allotment_to_json(row))
        else:
            allot_keys.add(key)
    missing = [
        {
            "position_id": p["position_id"],
            "applicant_id": p["applicant_id"],
            "sub_category": p["sub_category"],
        }
        for p in live
        if _pair_key(p["position_id"], p["applicant_id"]) not in allot_keys
    ]
    return {
        "missing_count": len(missing),
        "orphaned_count": len(orphaned),
        "missing": missing[:50],
        "orphaned": orphaned[:50],
    }


def list_allotments(ipo_id: str) -> dict[str, Any]:
    ipo = get_ipo(ipo_id, include_trade_count=False)
    rows = [allotment_to_json(r) for r in list_allotments_raw(ipo_id)]
    drift = drift_report(ipo_id)
    counts = {
        "pending": sum(1 for r in rows if r["status"] == "Pending"),
        "allotted": sum(1 for r in rows if r["status"] == "Allotted"),
        "not_allotted": sum(1 for r in rows if r["status"] == "Not Allotted"),
        "sold": sum(1 for r in rows if r["is_sold"]),
        "total": len(rows),
    }
    warnings: list[str] = []
    if ipo.get("status") == "Upcoming":
        warnings.append("IPO status is Upcoming.")
    return {
        "ipo": ipo,
        "allotments": rows,
        "counts": counts,
        "drift": drift,
        "warnings": warnings,
    }


def seed_allotments(ipo_id: str) -> dict[str, Any]:
    ipo = get_ipo(ipo_id, include_trade_count=False)
    warnings: list[str] = []
    if ipo.get("status") == "Upcoming":
        warnings.append("Seeding an Upcoming IPO.")

    live = _live_allocation_pairs(ipo_id)
    existing = list_allotments_raw(ipo_id, include_archived=True)
    existing_keys = {
        _pair_key(
            str(r["position_id"]) if r.get("position_id") else None,
            str(r.get("applicant_id") or ""),
        )
        for r in existing
        if r.get("position_id")
    }

    to_insert: list[dict[str, Any]] = []
    for p in live:
        key = _pair_key(p["position_id"], p["applicant_id"])
        if key in existing_keys:
            continue
        if not p.get("broker_id"):
            continue
        to_insert.append(
            {
                "ipo_id": ipo_id,
                "position_id": p["position_id"],
                "applicant_id": p["applicant_id"],
                "broker_id": p["broker_id"],
                "sub_category": p["sub_category"] or "",
                "cost_per_app": p["cost_per_app"],
                "status": "Pending",
                "shares_allotted": 0,
                "is_sold": False,
                "is_archived": False,
                "notes": "",
                "updated_at": _now(),
            }
        )
        if p["cost_per_app"] is None:
            warnings.append(f"Position {p['position_id']}: buy_app invalid; cost_per_app null.")

    inserted = 0
    for i in range(0, len(to_insert), CHUNK):
        chunk = to_insert[i : i + CHUNK]
        response = _http().post(
            f"{_supabase_url()}/rest/v1/{ALLOTMENTS_TABLE}",
            headers=_service_headers(
                {"Content-Type": "application/json", "Prefer": "return=minimal"}
            ),
            json=chunk,
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"Seed allotments failed ({response.status_code}): {response.text[:400]}"
            )
        inserted += len(chunk)

    result = list_allotments(ipo_id)
    result["seeded"] = inserted
    result["warnings"] = list(dict.fromkeys(warnings + result.get("warnings", [])))
    return result


def update_allotment(allotment_id: str, payload: AllotmentUpdate) -> dict[str, Any]:
    row = get_allotment(allotment_id)

    status = payload.status if payload.status is not None else row.get("status")
    shares = (
        int(payload.shares_allotted)
        if payload.shares_allotted is not None
        else int(row.get("shares_allotted") or 0)
    )
    if status not in ("Pending", "Allotted", "Not Allotted"):
        raise ValueError("Status must be Pending, Allotted, or Not Allotted.")
    if status == "Allotted" and shares <= 0:
        raise ValueError("Allotted requires shares_allotted > 0.")
    if status in ("Pending", "Not Allotted"):
        shares = 0

    patch: dict[str, Any] = {
        "status": status,
        "shares_allotted": shares,
        "updated_at": _now(),
    }
    if status == "Not Allotted":
        patch["listing_price_override"] = None
    if payload.listing_price_override is not None:
        patch["listing_price_override"] = payload.listing_price_override
    if payload.clear_listing_price_override:
        patch["listing_price_override"] = None
    if payload.notes is not None:
        patch["notes"] = payload.notes

    # Sold is a flag only — edits stay allowed. Refresh sold_price if still sold + allotted.
    if row.get("is_sold"):
        if status == "Allotted" and shares > 0:
            merged = {**row, **patch}
            ipo = get_ipo(str(row["ipo_id"]), include_trade_count=False)
            price = _effective_price(merged, ipo.get("listing_price"))
            if price is not None:
                patch["sold_price"] = float(_money(price))
        else:
            patch["is_sold"] = False
            patch["sold_price"] = None
            patch["sold_at"] = None

    response = _http().patch(
        f"{_supabase_url()}/rest/v1/{ALLOTMENTS_TABLE}",
        headers=_service_headers(
            {"Content-Type": "application/json", "Prefer": "return=representation"}
        ),
        params={"id": f"eq.{allotment_id}"},
        json=patch,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Update allotment failed ({response.status_code}): {response.text[:300]}")
    return allotment_to_json(get_allotment(allotment_id))


def bulk_update_allotments(items: list[Any]) -> dict[str, Any]:
    """Apply many allotment edits; returns {updated, errors}."""
    updated = 0
    errors: list[str] = []
    ipo_id: str | None = None
    for item in items:
        try:
            payload = AllotmentUpdate(
                status=getattr(item, "status", None),
                shares_allotted=getattr(item, "shares_allotted", None),
                listing_price_override=getattr(item, "listing_price_override", None),
                clear_listing_price_override=bool(
                    getattr(item, "clear_listing_price_override", False)
                ),
            )
            row = update_allotment(str(item.id), payload)
            ipo_id = row.get("ipo_id") or ipo_id
            updated += 1
        except Exception as exc:  # noqa: BLE001 — collect per-row errors for trader UI
            errors.append(f"{getattr(item, 'id', '?')}: {exc}")
    result: dict[str, Any] = {"updated": updated, "errors": errors}
    if ipo_id:
        result.update(list_allotments(ipo_id))
    return result


def _effective_price(row: dict[str, Any], listing_price: float | None) -> float | None:
    override = row.get("listing_price_override")
    if override is not None and override != "":
        return float(override)
    if listing_price is not None:
        return float(listing_price)
    return None


def sync_sold_prices_from_listing(ipo_id: str, listing_price: float | None) -> int:
    """Re-apply common listing price onto sold allotment rows for Settlement.

    Settlement reads frozen ``sold_price``. After Save common price we must rewrite
    sold_price for every sold Allotted row:
      - no row override → use common listing_price
      - has row override → keep override as sold_price
    """
    rows = list_allotments_raw(ipo_id)
    updated = 0
    for row in rows:
        if not row.get("is_sold"):
            continue
        if (row.get("status") or "") != "Allotted":
            continue
        if int(row.get("shares_allotted") or 0) <= 0:
            continue
        price = _effective_price(row, listing_price)
        if price is None:
            continue
        current = row.get("sold_price")
        if current is not None and abs(float(current) - float(price)) < 1e-9:
            continue
        response = _http().patch(
            f"{_supabase_url()}/rest/v1/{ALLOTMENTS_TABLE}",
            headers=_service_headers(
                {"Content-Type": "application/json", "Prefer": "return=minimal"}
            ),
            params={"id": f"eq.{row['id']}"},
            json={
                "sold_price": float(_money(price)),
                "updated_at": _now(),
            },
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code not in (200, 204):
            raise RuntimeError(
                f"Sync sold price failed ({response.status_code}): {response.text[:300]}"
            )
        updated += 1
    return updated


def set_common_listing_price(
    ipo_id: str, *, listing_price: float | None = None, clear: bool = False
) -> dict[str, Any]:
    """Save IPO common sold price and refresh sold_price on allotment rows."""
    get_ipo(ipo_id, include_trade_count=False)  # ensure exists
    if clear:
        next_price: float | None = None
    else:
        if listing_price is None:
            raise ValueError("listing_price is required unless clear=True.")
        next_price = float(listing_price)

    response = _http().patch(
        f"{_supabase_url()}/rest/v1/ipo_master",
        headers=_service_headers(
            {"Content-Type": "application/json", "Prefer": "return=minimal"}
        ),
        params={"id": f"eq.{ipo_id}"},
        json={"listing_price": next_price, "updated_at": _now()},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(
            f"Save common listing price failed ({response.status_code}): {response.text[:300]}"
        )

    synced = sync_sold_prices_from_listing(ipo_id, next_price)
    result = list_allotments(ipo_id)
    result["sold_prices_synced"] = synced
    return result


def mark_sold(ipo_id: str, sell_date: str) -> dict[str, Any]:
    ipo = get_ipo(ipo_id, include_trade_count=False)
    warnings: list[str] = []
    listing_date = (ipo.get("listing_date") or "")[:10]
    if not listing_date:
        warnings.append("IPO listing_date is not set.")
    elif sell_date < listing_date:
        warnings.append(f"Sell date {sell_date} is before listing_date {listing_date}.")

    listing_price = ipo.get("listing_price")
    rows = list_allotments_raw(ipo_id)
    candidates = [
        r
        for r in rows
        if r.get("status") == "Allotted"
        and not r.get("is_sold")
        and int(r.get("shares_allotted") or 0) > 0
        and not r.get("is_archived")
    ]
    if not candidates:
        result = list_allotments(ipo_id)
        result["marked"] = 0
        result["warnings"] = warnings + ["No unsold Allotted rows to mark."]
        return result

    by_price: dict[float, list[str]] = defaultdict(list)
    missing_price: list[str] = []
    for r in candidates:
        price = _effective_price(r, listing_price)
        if price is None:
            app = r.get("ipo_applicants") or {}
            name = app.get("name") if isinstance(app, dict) else r.get("id")
            missing_price.append(str(name))
            continue
        by_price[float(price)].append(str(r["id"]))

    if missing_price:
        raise ValueError(
            "Missing listing price for: "
            + ", ".join(missing_price[:10])
            + (f" (+{len(missing_price) - 10} more)" if len(missing_price) > 10 else "")
            + ". Set IPO listing price or a per-row override."
        )

    marked = 0
    for price, ids in by_price.items():
        for i in range(0, len(ids), CHUNK):
            chunk = ids[i : i + CHUNK]
            id_filter = "(" + ",".join(chunk) + ")"
            response = _http().patch(
                f"{_supabase_url()}/rest/v1/{ALLOTMENTS_TABLE}",
                headers=_service_headers(
                    {"Content-Type": "application/json", "Prefer": "return=minimal"}
                ),
                params={"id": f"in.{id_filter}", "is_sold": "eq.false"},
                json={
                    "is_sold": True,
                    "sold_price": float(_money(price)),
                    "sold_at": sell_date,
                    "updated_at": _now(),
                },
                timeout=HTTP_TIMEOUT,
            )
            if response.status_code not in (200, 204):
                raise RuntimeError(
                    f"Mark sold failed ({response.status_code}): {response.text[:300]}"
                )
            marked += len(chunk)

    result = list_allotments(ipo_id)
    result["marked"] = marked
    result["warnings"] = warnings
    return result


def mark_sold_selected(allotment_ids: list[str], sell_date: str) -> dict[str, Any]:
    """Mark only the given allotment rows as sold (must be Allotted with shares)."""
    if not allotment_ids:
        raise ValueError("Select at least one applicant.")

    warnings: list[str] = []
    marked = 0
    errors: list[str] = []
    ipo_id: str | None = None
    total_value = 0.0

    for allotment_id in allotment_ids:
        try:
            row = get_allotment(allotment_id)
            ipo_id = str(row["ipo_id"])
            if row.get("is_sold"):
                continue
            if row.get("status") != "Allotted" or int(row.get("shares_allotted") or 0) <= 0:
                name = ((row.get("ipo_applicants") or {}) if isinstance(row.get("ipo_applicants"), dict) else {}).get(
                    "name"
                ) or allotment_id
                errors.append(f"{name}: only Allotted rows with shares can be marked sold.")
                continue
            ipo = get_ipo(str(row["ipo_id"]), include_trade_count=False)
            price = _effective_price(row, ipo.get("listing_price"))
            if price is None:
                name = ((row.get("ipo_applicants") or {}) if isinstance(row.get("ipo_applicants"), dict) else {}).get(
                    "name"
                ) or allotment_id
                errors.append(f"{name}: set common sold price or a row sold price first.")
                continue
            response = _http().patch(
                f"{_supabase_url()}/rest/v1/{ALLOTMENTS_TABLE}",
                headers=_service_headers(
                    {"Content-Type": "application/json", "Prefer": "return=minimal"}
                ),
                params={"id": f"eq.{allotment_id}", "is_sold": "eq.false"},
                json={
                    "is_sold": True,
                    "sold_price": float(_money(price)),
                    "sold_at": sell_date,
                    "updated_at": _now(),
                },
                timeout=HTTP_TIMEOUT,
            )
            if response.status_code not in (200, 204):
                raise RuntimeError(
                    f"Mark sold failed ({response.status_code}): {response.text[:300]}"
                )
            shares = int(row.get("shares_allotted") or 0)
            total_value += float(_money(price)) * shares
            marked += 1
        except Exception as exc:  # noqa: BLE001 — collect per-row errors for trader UI
            errors.append(f"{allotment_id}: {exc}")

    if not ipo_id:
        raise ValueError("No valid allotment rows found.")
    if marked == 0 and errors:
        raise ValueError("; ".join(errors[:5]))

    result = list_allotments(ipo_id)
    result["marked"] = marked
    result["total_value"] = float(_money(total_value))
    result["errors"] = errors
    result["warnings"] = warnings
    return result


def mark_sold_row(allotment_id: str, sell_date: str) -> dict[str, Any]:
    row = get_allotment(allotment_id)
    if row.get("status") != "Allotted" or int(row.get("shares_allotted") or 0) <= 0:
        raise ValueError("Only Allotted rows with shares can be marked sold.")
    if row.get("is_sold"):
        return allotment_to_json(row)
    ipo = get_ipo(str(row["ipo_id"]), include_trade_count=False)
    price = _effective_price(row, ipo.get("listing_price"))
    if price is None:
        raise ValueError("Set IPO listing price or a listing price override first.")
    response = _http().patch(
        f"{_supabase_url()}/rest/v1/{ALLOTMENTS_TABLE}",
        headers=_service_headers(
            {"Content-Type": "application/json", "Prefer": "return=representation"}
        ),
        params={"id": f"eq.{allotment_id}"},
        json={
            "is_sold": True,
            "sold_price": float(_money(price)),
            "sold_at": sell_date,
            "updated_at": _now(),
        },
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Mark sold failed ({response.status_code}): {response.text[:300]}")
    return allotment_to_json(get_allotment(allotment_id))


def unmark_sold(allotment_id: str) -> dict[str, Any]:
    response = _http().patch(
        f"{_supabase_url()}/rest/v1/{ALLOTMENTS_TABLE}",
        headers=_service_headers(
            {"Content-Type": "application/json", "Prefer": "return=representation"}
        ),
        params={"id": f"eq.{allotment_id}"},
        json={
            "is_sold": False,
            "sold_price": None,
            "sold_at": None,
            "updated_at": _now(),
        },
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Unmark sold failed ({response.status_code}): {response.text[:300]}")
    return allotment_to_json(get_allotment(allotment_id))


def unmark_sold_selected(allotment_ids: list[str]) -> dict[str, Any]:
    """Clear sold flag on the given allotment rows."""
    if not allotment_ids:
        raise ValueError("Select at least one applicant.")

    unmarked = 0
    errors: list[str] = []
    ipo_id: str | None = None

    for allotment_id in allotment_ids:
        try:
            row = get_allotment(allotment_id)
            ipo_id = str(row["ipo_id"])
            if not row.get("is_sold"):
                continue
            unmark_sold(allotment_id)
            unmarked += 1
        except Exception as exc:  # noqa: BLE001 — collect per-row errors for trader UI
            errors.append(f"{allotment_id}: {exc}")

    if not ipo_id:
        raise ValueError("No valid allotment rows found.")
    if unmarked == 0 and errors:
        raise ValueError("; ".join(errors[:5]))

    result = list_allotments(ipo_id)
    result["unmarked"] = unmarked
    result["errors"] = errors
    return result


def archive_allotment(allotment_id: str) -> dict[str, Any]:
    response = _http().patch(
        f"{_supabase_url()}/rest/v1/{ALLOTMENTS_TABLE}",
        headers=_service_headers(
            {"Content-Type": "application/json", "Prefer": "return=representation"}
        ),
        params={"id": f"eq.{allotment_id}"},
        json={"is_archived": True, "updated_at": _now()},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Archive allotment failed ({response.status_code}): {response.text[:300]}")
    return allotment_to_json(get_allotment(allotment_id))
