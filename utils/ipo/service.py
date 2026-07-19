from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import httpx

from utils.ipo.categories import category_group_for
from utils.ipo.models import (
    IpoMasterCreate,
    IpoMasterUpdate,
    PositionCreate,
    PositionUpdate,
    SellCreate,
    SellUpdate,
    master_to_json,
    position_to_json,
    sell_to_json,
)
from utils.supabase_client import _service_headers, _supabase_url

MASTER_TABLE = "ipo_master"
POSITIONS_TABLE = "ipo_positions"
SELLS_TABLE = "ipo_sells"
LABELS_TABLE = "ipo_category_labels"
HTTP_TIMEOUT = 30.0
MONEY_QUANT = Decimal("0.0001")


def _money(value: float | int | Decimal | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


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


def get_ipo(ipo_id: str) -> dict[str, Any]:
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
        return master_to_json(row, trade_count=count_positions_for_ipo(str(row["id"])))
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


def _list_sells_for_positions(broker_id: str, position_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not position_ids:
        return {}
    # PostgREST in filter — chunk if needed
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
    return position_to_json(row, sold_app=sold_app, sells=sells, ipo=ipo)


def create_position(broker_id: str, payload: PositionCreate) -> dict[str, Any]:
    ipo = get_ipo(payload.ipo_id)
    if ipo.get("is_archived"):
        raise ValueError("Cannot trade against an archived IPO.")
    if ipo.get("status") != "Active":
        raise ValueError("Select an Active IPO. This IPO is not currently Active.")

    buy_amt = (_money(payload.buy_app) * _money(payload.buy_rate)).quantize(
        MONEY_QUANT, rounding=ROUND_HALF_UP
    )
    category = payload.category.strip()
    row = {
        "broker_id": broker_id,
        "ipo_id": payload.ipo_id,
        "trade_date": payload.trade_date,
        "party": payload.party.strip(),
        "category": category,
        "category_group": category_group_for(category),
        "applicant_name": (payload.applicant_name or "").strip(),
        "buy_app": float(_money(payload.buy_app)),
        "buy_rate": float(_money(payload.buy_rate)),
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
    return get_position(broker_id, str(created["id"]))


def update_position(broker_id: str, position_id: str, payload: PositionUpdate) -> dict[str, Any]:
    existing = get_position(broker_id, position_id)
    buy_app = payload.buy_app if payload.buy_app is not None else existing["buy_app"]
    if buy_app + 1e-9 < existing["sold_app"]:
        raise ValueError(
            f"BUY APP cannot be less than already sold quantity ({existing['sold_app']})."
        )
    ipo_id = payload.ipo_id or existing["ipo_id"]
    if payload.ipo_id:
        ipo = get_ipo(payload.ipo_id)
        if ipo.get("is_archived"):
            raise ValueError("Cannot move trade to an archived IPO.")

    buy_rate = payload.buy_rate if payload.buy_rate is not None else existing["buy_rate"]
    buy_amt = (_money(buy_app) * _money(buy_rate)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    category = (
        payload.category.strip() if payload.category is not None else existing["category"]
    )
    patch = {
        "ipo_id": ipo_id,
        "trade_date": payload.trade_date or existing["trade_date"],
        "party": payload.party.strip() if payload.party is not None else existing["party"],
        "category": category,
        "category_group": category_group_for(category),
        "applicant_name": (
            payload.applicant_name.strip()
            if payload.applicant_name is not None
            else existing["applicant_name"]
        ),
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
    row = {
        "broker_id": broker_id,
        "position_id": position_id,
        "sell_date": payload.sell_date,
        "sell_app": float(_money(payload.sell_app)),
        "sell_rate": float(_money(payload.sell_rate)),
        "sell_amt": float(sell_amt),
        "sell_party": payload.sell_party.strip(),
        "dalal": None if payload.dalal is None else float(_money(payload.dalal)),
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
    return {"sell": sell_to_json(created), "position": get_position(broker_id, position_id)}


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
    if payload.clear_dalal:
        dalal: float | None = None
    elif payload.dalal is not None:
        dalal = float(_money(payload.dalal))
    else:
        dalal = existing_sell["dalal"]

    patch = {
        "sell_date": payload.sell_date or existing_sell["sell_date"],
        "sell_app": float(_money(sell_app)),
        "sell_rate": float(_money(sell_rate)),
        "sell_amt": float(sell_amt),
        "sell_party": (
            payload.sell_party.strip()
            if payload.sell_party is not None
            else existing_sell["sell_party"]
        ),
        "dalal": dalal,
        "notes": payload.notes if payload.notes is not None else existing_sell["notes"],
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
