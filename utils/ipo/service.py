from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import httpx

from utils.ipo.categories import (
    category_group_for,
    is_premium,
    validate_category_pair,
)
from utils.ipo.clients import get_applicant, get_party
from utils.ipo.sell_parties import resolve_active_sell_party_name
from utils.ipo.models import (
    AllocationSet,
    IpoMasterCreate,
    IpoMasterUpdate,
    PositionCreate,
    PositionUpdate,
    SellCreate,
    SellUpdate,
    applicant_to_json,
    compute_position_status,
    master_to_json,
    position_to_json,
    sell_to_json,
)
from utils.supabase_client import _service_headers, _supabase_url

MASTER_TABLE = "ipo_master"
POSITIONS_TABLE = "ipo_positions"
SELLS_TABLE = "ipo_sells"
ALLOCATIONS_TABLE = "ipo_position_allocations"
APPLICANTS_TABLE = "ipo_applicants"
LABELS_TABLE = "ipo_category_labels"
HTTP_TIMEOUT = 30.0
MONEY_QUANT = Decimal("0.0001")


def _money(value: float | int | Decimal | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _calc_brokerage(
    sell_app: float | Decimal, sell_rate: float | Decimal, buy_rate: float | Decimal
) -> Decimal:
    """Earned brokerage = sell amount − buy amount for the sold quantity."""
    sell_amt = _money(sell_app) * _money(sell_rate)
    buy_amt_for_sold = _money(sell_app) * _money(buy_rate)
    return (sell_amt - buy_amt_for_sold).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_category_labels() -> list[dict[str, Any]]:
    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{LABELS_TABLE}",
        headers=_service_headers(),
        params={
            "select": "code,category_group,display_order,is_active",
            "is_active": "eq.true",
            "order": "display_order.asc",
        },
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        from utils.ipo.categories import CATEGORY_SEEDS

        return [
            {
                "code": str(row["code"]),
                "category_group": str(row["category_group"]),
                "display_order": int(row["display_order"]),
                "is_active": True,
            }
            for row in CATEGORY_SEEDS
        ]
    data = response.json()
    return data if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# IPO Master
# ---------------------------------------------------------------------------


def count_positions_for_ipo(ipo_id: str) -> int:
    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{POSITIONS_TABLE}",
        headers={**_service_headers(), "Prefer": "count=exact"},
        params={"select": "id", "ipo_id": f"eq.{ipo_id}", "limit": "1"},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 206):
        raise RuntimeError(f"Count positions failed ({response.status_code}): {response.text[:300]}")
    content_range = response.headers.get("content-range") or "*/0"
    total = content_range.split("/")[-1]
    try:
        return int(total)
    except ValueError:
        data = response.json()
        return len(data) if isinstance(data, list) else 0


def list_ipos(
    *,
    status: str | None = None,
    include_archived: bool = False,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    params: dict[str, str] = {
        "select": "*",
        "order": "updated_at.desc",
        "limit": "500",
    }
    if active_only:
        params["status"] = "eq.Active"
        params["is_archived"] = "eq.false"
    elif status:
        params["status"] = f"eq.{status}"
    if not include_archived and not active_only:
        params["is_archived"] = "eq.false"

    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{MASTER_TABLE}",
        headers=_service_headers(),
        params=params,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"List IPOs failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    rows = data if isinstance(data, list) else []
    return [master_to_json(row, trade_count=count_positions_for_ipo(str(row["id"]))) for row in rows]


def get_ipo(ipo_id: str, *, include_trade_count: bool = True) -> dict[str, Any]:
    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{MASTER_TABLE}",
        headers=_service_headers(),
        params={"select": "*", "id": f"eq.{ipo_id}", "limit": "1"},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Fetch IPO failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    if isinstance(data, list) and data:
        row = data[0]
        trade_count = count_positions_for_ipo(str(row["id"])) if include_trade_count else None
        return master_to_json(row, trade_count=trade_count)
    raise LookupError("IPO not found.")


def create_ipo(payload: IpoMasterCreate) -> dict[str, Any]:
    display = payload.display_name.strip() or payload.name.strip()
    row = {
        "name": payload.name.strip(),
        "display_name": display,
        "open_date": payload.open_date or None,
        "close_date": payload.close_date or None,
        "listing_date": payload.listing_date or None,
        "status": payload.status,
        "notes": payload.notes or "",
        "amount_bhni": payload.amount_bhni,
        "amount_shni": payload.amount_shni,
        "amount_retail_15k": payload.amount_retail_15k,
        "amount_retail_2minus": payload.amount_retail_2minus,
        "amount_shareholder_15k": payload.amount_shareholder_15k,
        "amount_shareholder_2minus": payload.amount_shareholder_2minus,
        "listing_price": payload.listing_price,
        "is_archived": False,
        "updated_at": _now(),
    }
    response = httpx.post(
        f"{_supabase_url()}/rest/v1/{MASTER_TABLE}",
        headers=_service_headers(
            {"Content-Type": "application/json", "Prefer": "return=representation"}
        ),
        json=row,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Create IPO failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    created = data[0] if isinstance(data, list) and data else data
    return master_to_json(created, trade_count=0)


def update_ipo(ipo_id: str, payload: IpoMasterUpdate) -> dict[str, Any]:
    existing = get_ipo(ipo_id)
    patch: dict[str, Any] = {"updated_at": _now()}
    if payload.name is not None:
        patch["name"] = payload.name.strip()
    if payload.display_name is not None:
        patch["display_name"] = payload.display_name.strip() or existing["name"]
    if payload.open_date is not None:
        patch["open_date"] = payload.open_date or None
    if payload.close_date is not None:
        patch["close_date"] = payload.close_date or None
    if payload.listing_date is not None:
        patch["listing_date"] = payload.listing_date or None
    if payload.status is not None:
        patch["status"] = payload.status
    if payload.notes is not None:
        patch["notes"] = payload.notes
    if payload.clear_amount_bhni:
        patch["amount_bhni"] = None
    elif payload.amount_bhni is not None:
        patch["amount_bhni"] = payload.amount_bhni
    if payload.clear_amount_shni:
        patch["amount_shni"] = None
    elif payload.amount_shni is not None:
        patch["amount_shni"] = payload.amount_shni
    if payload.clear_amount_retail_15k:
        patch["amount_retail_15k"] = None
    elif payload.amount_retail_15k is not None:
        patch["amount_retail_15k"] = payload.amount_retail_15k
    if payload.clear_amount_retail_2minus:
        patch["amount_retail_2minus"] = None
    elif payload.amount_retail_2minus is not None:
        patch["amount_retail_2minus"] = payload.amount_retail_2minus
    if payload.clear_amount_shareholder_15k:
        patch["amount_shareholder_15k"] = None
    elif payload.amount_shareholder_15k is not None:
        patch["amount_shareholder_15k"] = payload.amount_shareholder_15k
    if payload.clear_amount_shareholder_2minus:
        patch["amount_shareholder_2minus"] = None
    elif payload.amount_shareholder_2minus is not None:
        patch["amount_shareholder_2minus"] = payload.amount_shareholder_2minus
    if payload.clear_listing_price:
        patch["listing_price"] = None
    elif payload.listing_price is not None:
        patch["listing_price"] = payload.listing_price
    if payload.is_archived is not None:
        patch["is_archived"] = payload.is_archived

    response = httpx.patch(
        f"{_supabase_url()}/rest/v1/{MASTER_TABLE}",
        headers=_service_headers(
            {"Content-Type": "application/json", "Prefer": "return=representation"}
        ),
        params={"id": f"eq.{ipo_id}"},
        json=patch,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Update IPO failed ({response.status_code}): {response.text[:300]}")
    return get_ipo(ipo_id)


def delete_ipo(ipo_id: str) -> None:
    trade_count = count_positions_for_ipo(ipo_id)
    if trade_count > 0:
        raise PermissionError(
            f"This IPO cannot be deleted because it is associated with {trade_count} trade"
            f"{'s' if trade_count != 1 else ''}. Deleting it would orphan historical trading data. "
            "Please archive the IPO instead or remove all associated trades before deleting."
        )
    response = httpx.delete(
        f"{_supabase_url()}/rest/v1/{MASTER_TABLE}",
        headers=_service_headers({"Prefer": "return=minimal"}),
        params={"id": f"eq.{ipo_id}"},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Delete IPO failed ({response.status_code}): {response.text[:300]}")


def archive_ipo(ipo_id: str) -> dict[str, Any]:
    return update_ipo(ipo_id, IpoMasterUpdate(is_archived=True, status="Closed"))


# ---------------------------------------------------------------------------
# Positions (buy) + Sells
# ---------------------------------------------------------------------------


def _list_allocations_for_positions(
    position_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    if not position_ids:
        return {}
    by_pos: dict[str, list[dict[str, Any]]] = {pid: [] for pid in position_ids}
    links: list[tuple[str, str]] = []
    applicant_ids: set[str] = set()
    for i in range(0, len(position_ids), 50):
        chunk = position_ids[i : i + 50]
        response = httpx.get(
            f"{_supabase_url()}/rest/v1/{ALLOCATIONS_TABLE}",
            headers=_service_headers(),
            params={
                "select": "position_id,applicant_id",
                "position_id": f"in.({','.join(chunk)})",
                "limit": "5000",
            },
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"List allocations failed ({response.status_code}): {response.text[:300]}"
            )
        data = response.json()
        for row in data if isinstance(data, list) else []:
            pid = str(row.get("position_id") or "")
            aid = str(row.get("applicant_id") or "")
            if pid and aid:
                links.append((pid, aid))
                applicant_ids.add(aid)

    applicants: dict[str, dict[str, Any]] = {}
    id_list = list(applicant_ids)
    for i in range(0, len(id_list), 50):
        chunk = id_list[i : i + 50]
        response = httpx.get(
            f"{_supabase_url()}/rest/v1/{APPLICANTS_TABLE}",
            headers=_service_headers(),
            params={"select": "*", "id": f"in.({','.join(chunk)})"},
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Fetch applicants failed ({response.status_code}): {response.text[:300]}"
            )
        data = response.json()
        for row in data if isinstance(data, list) else []:
            applicants[str(row["id"])] = applicant_to_json(row)

    for pid, aid in links:
        app = applicants.get(aid)
        if app:
            by_pos.setdefault(pid, []).append(app)
    for pid in by_pos:
        by_pos[pid].sort(key=lambda a: (a.get("name") or "").upper())
    return by_pos


def _list_sells_for_positions(broker_id: str, position_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not position_ids:
        return {}
    by_pos: dict[str, list[dict[str, Any]]] = {pid: [] for pid in position_ids}
    chunk_size = 50
    for i in range(0, len(position_ids), chunk_size):
        chunk = position_ids[i : i + chunk_size]
        ids_filter = ",".join(chunk)
        response = httpx.get(
            f"{_supabase_url()}/rest/v1/{SELLS_TABLE}",
            headers=_service_headers(),
            params={
                "select": "*",
                "broker_id": f"eq.{broker_id}",
                "position_id": f"in.({ids_filter})",
                "order": "sell_date.asc,created_at.asc",
                "limit": "5000",
            },
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code != 200:
            raise RuntimeError(f"List sells failed ({response.status_code}): {response.text[:300]}")
        data = response.json()
        for row in data if isinstance(data, list) else []:
            pid = str(row.get("position_id") or "")
            by_pos.setdefault(pid, []).append(sell_to_json(row))
    return by_pos


def _ipo_map(ipo_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not ipo_ids:
        return {}
    unique = list(dict.fromkeys(ipo_ids))
    result: dict[str, dict[str, Any]] = {}
    for i in range(0, len(unique), 50):
        chunk = unique[i : i + 50]
        response = httpx.get(
            f"{_supabase_url()}/rest/v1/{MASTER_TABLE}",
            headers=_service_headers(),
            params={"select": "*", "id": f"in.({','.join(chunk)})"},
            timeout=HTTP_TIMEOUT,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Fetch IPOs failed ({response.status_code}): {response.text[:300]}")
        data = response.json()
        for row in data if isinstance(data, list) else []:
            result[str(row["id"])] = master_to_json(row)
    return result


def list_positions(
    broker_id: str,
    *,
    ipo_id: str | None = None,
    status: str | None = None,
    party: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, str] = {
        "select": "*",
        "broker_id": f"eq.{broker_id}",
        "order": "trade_date.desc,created_at.desc",
        "limit": "2000",
    }
    if ipo_id:
        params["ipo_id"] = f"eq.{ipo_id}"
    if party and party.strip():
        params["party"] = f"ilike.*{party.strip()}*"
    if date_from and date_to:
        params["and"] = f"(trade_date.gte.{date_from},trade_date.lte.{date_to})"
    elif date_from:
        params["trade_date"] = f"gte.{date_from}"
    elif date_to:
        params["trade_date"] = f"lte.{date_to}"

    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{POSITIONS_TABLE}",
        headers=_service_headers(),
        params=params,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"List positions failed ({response.status_code}): {response.text[:300]}")
    rows = response.json()
    if not isinstance(rows, list):
        rows = []

    position_ids = [str(r["id"]) for r in rows]
    sells_by_pos = _list_sells_for_positions(broker_id, position_ids)
    allocs_by_pos = _list_allocations_for_positions(position_ids)
    ipos = _ipo_map([str(r["ipo_id"]) for r in rows])

    result: list[dict[str, Any]] = []
    for row in rows:
        pid = str(row["id"])
        sells = sells_by_pos.get(pid, [])
        sold_app = sum(s["sell_app"] for s in sells)
        payload = position_to_json(
            row,
            sold_app=sold_app,
            sells=sells,
            ipo=ipos.get(str(row["ipo_id"])),
            allocations=allocs_by_pos.get(pid, []),
        )
        if status and payload["status"] != status:
            continue
        result.append(payload)
    return result


def get_position(broker_id: str, position_id: str) -> dict[str, Any]:
    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{POSITIONS_TABLE}",
        headers=_service_headers(),
        params={
            "select": "*",
            "id": f"eq.{position_id}",
            "broker_id": f"eq.{broker_id}",
            "limit": "1",
        },
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Fetch position failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    if not (isinstance(data, list) and data):
        raise LookupError("Position not found.")
    row = data[0]
    sells = _list_sells_for_positions(broker_id, [position_id]).get(position_id, [])
    sold_app = sum(s["sell_app"] for s in sells)
    ipo = _ipo_map([str(row["ipo_id"])]).get(str(row["ipo_id"]))
    allocations = _list_allocations_for_positions([position_id]).get(position_id, [])
    return position_to_json(
        row, sold_app=sold_app, sells=sells, ipo=ipo, allocations=allocations
    )


def create_position(broker_id: str, payload: PositionCreate) -> dict[str, Any]:
    ipo = get_ipo(payload.ipo_id, include_trade_count=False)
    if ipo.get("is_archived"):
        raise ValueError("Cannot trade against an archived IPO.")
    if ipo.get("status") != "Active":
        raise ValueError("Select an Active IPO. This IPO is not currently Active.")

    party = get_party(payload.party_id, include_applicant_count=False)
    if party.get("is_archived") or party.get("status") != "Active":
        raise ValueError("Selected party is not Active.")
    party_name = party.get("name") or ""

    category, sub_category = validate_category_pair(payload.category, payload.sub_category)
    buy_rate = float(_money(payload.buy_rate))

    buy_amt = (_money(payload.buy_app) * _money(buy_rate)).quantize(
        MONEY_QUANT, rounding=ROUND_HALF_UP
    )
    row = {
        "broker_id": broker_id,
        "ipo_id": payload.ipo_id,
        "trade_date": payload.trade_date,
        "party_id": payload.party_id,
        "party": party_name,
        "category": category,
        "sub_category": sub_category,
        "category_group": category_group_for(sub_category) or category,
        "applicant_id": None,
        "applicant_name": "",
        "buy_app": float(_money(payload.buy_app)),
        "buy_rate": buy_rate,
        "buy_amt": float(buy_amt),
        "updated_at": _now(),
    }
    response = httpx.post(
        f"{_supabase_url()}/rest/v1/{POSITIONS_TABLE}",
        headers=_service_headers(
            {"Content-Type": "application/json", "Prefer": "return=representation"}
        ),
        json=row,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Create position failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    created = data[0] if isinstance(data, list) and data else data
    position_id = str(created["id"])
    position = position_to_json(created, sold_app=0.0, sells=[], ipo=ipo, allocations=[])

    if not payload.include_sell:
        return position

    # Optional same-form sell — no extra get_position round-trip
    sell_app = float(payload.sell_app)  # type: ignore[arg-type]
    sell_rate = float(payload.sell_rate)  # type: ignore[arg-type]
    if sell_app > float(position["buy_app"]) + 1e-9:
        raise ValueError(
            f"SELL APP ({sell_app}) cannot exceed BUY APP ({position['buy_app']})."
        )
    sell_amt = (_money(sell_app) * _money(sell_rate)).quantize(
        MONEY_QUANT, rounding=ROUND_HALF_UP
    )
    brokerage = _calc_brokerage(sell_app, sell_rate, position["buy_rate"])
    sell_party_name = resolve_active_sell_party_name(payload.sell_party)
    sell_row = {
        "broker_id": broker_id,
        "position_id": position_id,
        "sell_date": payload.sell_date or payload.trade_date,
        "sell_app": float(_money(sell_app)),
        "sell_rate": float(_money(sell_rate)),
        "sell_amt": float(sell_amt),
        "sell_party": sell_party_name,
        "brokerage": float(brokerage),
        "notes": "",
        "updated_at": _now(),
    }
    sell_response = httpx.post(
        f"{_supabase_url()}/rest/v1/{SELLS_TABLE}",
        headers=_service_headers(
            {"Content-Type": "application/json", "Prefer": "return=representation"}
        ),
        json=sell_row,
        timeout=HTTP_TIMEOUT,
    )
    if sell_response.status_code not in (200, 201):
        raise RuntimeError(
            f"Create sell failed ({sell_response.status_code}): {sell_response.text[:300]}"
        )
    sell_data = sell_response.json()
    sell_created = sell_data[0] if isinstance(sell_data, list) and sell_data else sell_data
    sell = sell_to_json(sell_created)
    buy_app = float(position["buy_app"])
    return {
        **position,
        "sells": [sell],
        "sold_app": sell_app,
        "remaining_app": max(buy_app - sell_app, 0.0),
        "status": compute_position_status(buy_app, sell_app),
    }


def update_position(broker_id: str, position_id: str, payload: PositionUpdate) -> dict[str, Any]:
    existing = get_position(broker_id, position_id)
    buy_app = payload.buy_app if payload.buy_app is not None else existing["buy_app"]
    if buy_app + 1e-9 < existing["sold_app"]:
        raise ValueError(
            f"BUY APP cannot be less than already sold quantity ({existing['sold_app']}). "
            "Remove or reduce sells first."
        )
    allocated = int(existing.get("allocated_count") or 0)
    if allocated > 0 and buy_app + 1e-9 < allocated:
        raise ValueError(
            f"BUY APP cannot be less than allocated applicants ({allocated}). "
            "Clear or re-allocate applicants first."
        )

    ipo_id = payload.ipo_id or existing["ipo_id"]
    ipo = get_ipo(ipo_id, include_trade_count=False)
    if payload.ipo_id and payload.ipo_id != existing["ipo_id"]:
        if ipo.get("is_archived"):
            raise ValueError("Cannot move trade to an archived IPO.")
        if ipo.get("status") != "Active":
            raise ValueError("Select an Active IPO when changing the IPO.")

    category = existing["category"]
    sub_category = existing.get("sub_category") or ""
    if payload.category is not None or payload.sub_category is not None:
        category, sub_category = validate_category_pair(
            payload.category if payload.category is not None else existing["category"],
            payload.sub_category
            if payload.sub_category is not None
            else (existing.get("sub_category") or ""),
        )

    buy_rate = payload.buy_rate if payload.buy_rate is not None else existing["buy_rate"]
    buy_amt = (_money(buy_app) * _money(buy_rate)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)

    party_id = existing.get("party_id")
    party_name = existing["party"]
    if payload.party_id is not None and payload.party_id != existing.get("party_id"):
        party = get_party(payload.party_id, include_applicant_count=False)
        if party.get("is_archived") or party.get("status") != "Active":
            raise ValueError("Selected party is not Active.")
        if allocated > 0:
            raise ValueError(
                "Clear applicant allocation before changing the buy party."
            )
        party_id = payload.party_id
        party_name = party.get("name") or party_name
    elif payload.party_id is not None:
        party_id = payload.party_id
        if not party_name:
            party = get_party(payload.party_id, include_applicant_count=False)
            party_name = party.get("name") or party_name

    patch = {
        "ipo_id": ipo_id,
        "trade_date": payload.trade_date or existing["trade_date"],
        "party_id": party_id,
        "party": party_name,
        "category": category,
        "sub_category": sub_category,
        "category_group": category_group_for(sub_category) or category,
        "buy_app": float(_money(buy_app)),
        "buy_rate": float(_money(buy_rate)),
        "buy_amt": float(buy_amt),
        "updated_at": _now(),
    }
    response = httpx.patch(
        f"{_supabase_url()}/rest/v1/{POSITIONS_TABLE}",
        headers=_service_headers(
            {"Content-Type": "application/json", "Prefer": "return=representation"}
        ),
        params={"id": f"eq.{position_id}", "broker_id": f"eq.{broker_id}"},
        json=patch,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Update position failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    if isinstance(data, list) and data:
        row = data[0]
        return position_to_json(
            row,
            sold_app=existing["sold_app"],
            sells=existing.get("sells") or [],
            ipo=ipo if isinstance(ipo, dict) else existing.get("ipo"),
            allocations=existing.get("allocations") or [],
        )
    return get_position(broker_id, position_id)


def delete_position(broker_id: str, position_id: str) -> None:
    get_position(broker_id, position_id)
    response = httpx.delete(
        f"{_supabase_url()}/rest/v1/{POSITIONS_TABLE}",
        headers=_service_headers({"Prefer": "return=minimal"}),
        params={"id": f"eq.{position_id}", "broker_id": f"eq.{broker_id}"},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Delete position failed ({response.status_code}): {response.text[:300]}")


def create_sell(broker_id: str, position_id: str, payload: SellCreate) -> dict[str, Any]:
    position = get_position(broker_id, position_id)
    remaining = position["remaining_app"]
    if payload.sell_app > remaining + 1e-9:
        raise ValueError(
            f"Cannot sell {payload.sell_app}: only {remaining} applications remaining on this position."
        )
    sell_amt = (_money(payload.sell_app) * _money(payload.sell_rate)).quantize(
        MONEY_QUANT, rounding=ROUND_HALF_UP
    )
    brokerage = _calc_brokerage(payload.sell_app, payload.sell_rate, position["buy_rate"])
    sell_party_name = resolve_active_sell_party_name(payload.sell_party)
    row = {
        "broker_id": broker_id,
        "position_id": position_id,
        "sell_date": payload.sell_date,
        "sell_app": float(_money(payload.sell_app)),
        "sell_rate": float(_money(payload.sell_rate)),
        "sell_amt": float(sell_amt),
        "sell_party": sell_party_name,
        "brokerage": float(brokerage),
        "notes": payload.notes or "",
        "updated_at": _now(),
    }
    response = httpx.post(
        f"{_supabase_url()}/rest/v1/{SELLS_TABLE}",
        headers=_service_headers(
            {"Content-Type": "application/json", "Prefer": "return=representation"}
        ),
        json=row,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Create sell failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    created = data[0] if isinstance(data, list) and data else data
    sell = sell_to_json(created)
    sells = list(position.get("sells") or []) + [sell]
    sold_app = sum(float(s["sell_app"]) for s in sells)
    buy_app = float(position["buy_app"])
    position_out = {
        **position,
        "sells": sells,
        "sold_app": sold_app,
        "remaining_app": max(buy_app - sold_app, 0.0),
        "status": compute_position_status(buy_app, sold_app),
    }
    return {"sell": sell, "position": position_out}


def update_sell(
    broker_id: str, position_id: str, sell_id: str, payload: SellUpdate
) -> dict[str, Any]:
    position = get_position(broker_id, position_id)
    existing_sell = next((s for s in position["sells"] if s["id"] == sell_id), None)
    if existing_sell is None:
        raise LookupError("Sell transaction not found.")

    sell_app = payload.sell_app if payload.sell_app is not None else existing_sell["sell_app"]
    other_sold = position["sold_app"] - existing_sell["sell_app"]
    if other_sold + sell_app > position["buy_app"] + 1e-9:
        raise ValueError(
            f"Cannot set sell quantity to {sell_app}: would exceed BUY APP "
            f"({position['buy_app']}; other sells already {other_sold})."
        )

    sell_rate = payload.sell_rate if payload.sell_rate is not None else existing_sell["sell_rate"]
    sell_amt = (_money(sell_app) * _money(sell_rate)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    brokerage = _calc_brokerage(sell_app, sell_rate, position["buy_rate"])

    patch = {
        "sell_date": payload.sell_date or existing_sell["sell_date"],
        "sell_app": float(_money(sell_app)),
        "sell_rate": float(_money(sell_rate)),
        "sell_amt": float(sell_amt),
        "sell_party": (
            resolve_active_sell_party_name(payload.sell_party)
            if payload.sell_party is not None
            else existing_sell["sell_party"]
        ),
        "brokerage": float(brokerage),
        "notes": payload.notes if payload.notes is not None else existing_sell.get("notes") or "",
        "updated_at": _now(),
    }
    response = httpx.patch(
        f"{_supabase_url()}/rest/v1/{SELLS_TABLE}",
        headers=_service_headers(
            {"Content-Type": "application/json", "Prefer": "return=representation"}
        ),
        params={
            "id": f"eq.{sell_id}",
            "broker_id": f"eq.{broker_id}",
            "position_id": f"eq.{position_id}",
        },
        json=patch,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Update sell failed ({response.status_code}): {response.text[:300]}")
    return {"position": get_position(broker_id, position_id)}


def delete_sell(broker_id: str, position_id: str, sell_id: str) -> dict[str, Any]:
    position = get_position(broker_id, position_id)
    if not any(s["id"] == sell_id for s in position["sells"]):
        raise LookupError("Sell transaction not found.")
    response = httpx.delete(
        f"{_supabase_url()}/rest/v1/{SELLS_TABLE}",
        headers=_service_headers({"Prefer": "return=minimal"}),
        params={
            "id": f"eq.{sell_id}",
            "broker_id": f"eq.{broker_id}",
            "position_id": f"eq.{position_id}",
        },
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Delete sell failed ({response.status_code}): {response.text[:300]}")
    return get_position(broker_id, position_id)


def set_position_allocations(
    broker_id: str, position_id: str, payload: AllocationSet
) -> dict[str, Any]:
    """
    Allocate applicants from the buy party onto a position.
    Count must exactly equal BUY APP (whole number) to mark Fully Allocated.
    Not used for Premium (share) trades.
    """
    position = get_position(broker_id, position_id)
    if is_premium(position.get("category")):
        raise ValueError(
            "Applicant allocation is for IPO Application / Subject 2 trades only. "
            "Premium trades use number of shares, not applications."
        )
    buy_app = float(position["buy_app"])
    required = int(round(buy_app))
    if abs(buy_app - required) > 1e-9:
        raise ValueError(
            f"BUY APP must be a whole number to allocate applicants (currently {buy_app})."
        )
    if len(payload.applicant_ids) != required:
        raise ValueError(
            f"Select exactly {required} applicant(s) to match BUY APP ({required}). "
            f"You selected {len(payload.applicant_ids)}."
        )

    party_id = position.get("party_id")
    if not party_id:
        raise ValueError("This position has no Client Master party. Edit the buy party first.")

    validated: list[dict[str, Any]] = []
    for aid in payload.applicant_ids:
        app = get_applicant(aid)
        if app.get("is_archived") or app.get("status") != "Active":
            raise ValueError(f"Applicant '{app.get('name')}' is not Active.")
        if app.get("party_id") != party_id:
            raise ValueError(
                f"Applicant '{app.get('name')}' does not belong to buy party '{position.get('party')}'."
            )
        validated.append(app)

    # Replace allocation set
    httpx.delete(
        f"{_supabase_url()}/rest/v1/{ALLOCATIONS_TABLE}",
        headers=_service_headers({"Prefer": "return=minimal"}),
        params={"position_id": f"eq.{position_id}"},
        timeout=HTTP_TIMEOUT,
    )
    rows = [{"position_id": position_id, "applicant_id": a["id"]} for a in validated]
    response = httpx.post(
        f"{_supabase_url()}/rest/v1/{ALLOCATIONS_TABLE}",
        headers=_service_headers(
            {"Content-Type": "application/json", "Prefer": "return=minimal"}
        ),
        json=rows,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"Save allocations failed ({response.status_code}): {response.text[:300]}"
        )

    # Denormalize names onto position for quick display
    names = ", ".join(a.get("name") or "" for a in validated)
    httpx.patch(
        f"{_supabase_url()}/rest/v1/{POSITIONS_TABLE}",
        headers=_service_headers(
            {"Content-Type": "application/json", "Prefer": "return=minimal"}
        ),
        params={"id": f"eq.{position_id}", "broker_id": f"eq.{broker_id}"},
        json={"applicant_name": names, "applicant_id": None, "updated_at": _now()},
        timeout=HTTP_TIMEOUT,
    )
    return get_position(broker_id, position_id)


def clear_position_allocations(broker_id: str, position_id: str) -> dict[str, Any]:
    get_position(broker_id, position_id)
    response = httpx.delete(
        f"{_supabase_url()}/rest/v1/{ALLOCATIONS_TABLE}",
        headers=_service_headers({"Prefer": "return=minimal"}),
        params={"position_id": f"eq.{position_id}"},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(
            f"Clear allocations failed ({response.status_code}): {response.text[:300]}"
        )
    httpx.patch(
        f"{_supabase_url()}/rest/v1/{POSITIONS_TABLE}",
        headers=_service_headers(
            {"Content-Type": "application/json", "Prefer": "return=minimal"}
        ),
        params={"id": f"eq.{position_id}", "broker_id": f"eq.{broker_id}"},
        json={"applicant_name": "", "applicant_id": None, "updated_at": _now()},
        timeout=HTTP_TIMEOUT,
    )
    return get_position(broker_id, position_id)
