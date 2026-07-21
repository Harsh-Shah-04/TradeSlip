"""Sell Party master — counterparties used when recording sells."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from utils.ipo.models import SellPartyCreate, SellPartyUpdate, sell_party_to_json
from utils.supabase_client import _service_headers, _supabase_url

SELL_PARTIES_TABLE = "ipo_sell_parties"
SELLS_TABLE = "ipo_sells"
HTTP_TIMEOUT = 30.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_sell_parties(
    *,
    include_archived: bool = False,
    status: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, str] = {
        "select": "*",
        "order": "name.asc",
        "limit": "2000",
    }
    if not include_archived:
        params["is_archived"] = "eq.false"
    if status:
        params["status"] = f"eq.{status}"
    if search and search.strip():
        params["name"] = f"ilike.*{search.strip()}*"

    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{SELL_PARTIES_TABLE}",
        headers=_service_headers(),
        params=params,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"List sell parties failed ({response.status_code}): {response.text[:300]}"
        )
    rows = response.json()
    if not isinstance(rows, list):
        rows = []
    counts = _sell_trade_counts([str(r["id"]) for r in rows], [r.get("name") or "" for r in rows])
    return [
        sell_party_to_json(r, trade_count=counts.get((r.get("name") or "").casefold(), 0))
        for r in rows
    ]


def get_sell_party(sell_party_id: str) -> dict[str, Any]:
    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{SELL_PARTIES_TABLE}",
        headers=_service_headers(),
        params={"select": "*", "id": f"eq.{sell_party_id}", "limit": "1"},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Fetch sell party failed ({response.status_code}): {response.text[:300]}"
        )
    data = response.json()
    if not (isinstance(data, list) and data):
        raise LookupError("Sell party not found.")
    row = data[0]
    name = row.get("name") or ""
    counts = _sell_trade_counts([str(row["id"])], [name])
    return sell_party_to_json(row, trade_count=counts.get(name.casefold(), 0))


def create_sell_party(payload: SellPartyCreate) -> dict[str, Any]:
    row = {
        "name": payload.name.strip(),
        "notes": payload.notes or "",
        "status": payload.status,
        "is_archived": False,
        "updated_at": _now(),
    }
    response = httpx.post(
        f"{_supabase_url()}/rest/v1/{SELL_PARTIES_TABLE}",
        headers=_service_headers(
            {"Content-Type": "application/json", "Prefer": "return=representation"}
        ),
        json=row,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code in (409,):
        raise ValueError("A sell party with this name already exists.")
    if response.status_code not in (200, 201):
        text = response.text[:300]
        if "idx_ipo_sell_parties_name_lower" in text or "duplicate" in text.lower():
            raise ValueError("A sell party with this name already exists.")
        raise RuntimeError(f"Create sell party failed ({response.status_code}): {text}")
    data = response.json()
    created = data[0] if isinstance(data, list) and data else data
    return sell_party_to_json(created, trade_count=0)


def update_sell_party(sell_party_id: str, payload: SellPartyUpdate) -> dict[str, Any]:
    existing = get_sell_party(sell_party_id)
    patch: dict[str, Any] = {"updated_at": _now()}
    old_name = existing.get("name") or ""
    new_name = old_name
    if payload.name is not None:
        new_name = payload.name.strip()
        patch["name"] = new_name
    if payload.notes is not None:
        patch["notes"] = payload.notes
    if payload.status is not None:
        patch["status"] = payload.status
    if payload.is_archived is not None:
        patch["is_archived"] = payload.is_archived

    response = httpx.patch(
        f"{_supabase_url()}/rest/v1/{SELL_PARTIES_TABLE}",
        headers=_service_headers(
            {"Content-Type": "application/json", "Prefer": "return=representation"}
        ),
        params={"id": f"eq.{sell_party_id}"},
        json=patch,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        text = response.text[:300]
        if "idx_ipo_sell_parties_name_lower" in text or "duplicate" in text.lower():
            raise ValueError("A sell party with this name already exists.")
        raise RuntimeError(f"Update sell party failed ({response.status_code}): {text}")

    # Keep historical sells in sync when renaming
    if new_name and new_name != old_name:
        httpx.patch(
            f"{_supabase_url()}/rest/v1/{SELLS_TABLE}",
            headers=_service_headers({"Content-Type": "application/json"}),
            params={"sell_party": f"eq.{old_name}"},
            json={"sell_party": new_name, "updated_at": _now()},
            timeout=HTTP_TIMEOUT,
        )

    return get_sell_party(sell_party_id)


def resolve_active_sell_party_name(raw: str | None) -> str:
    """Validate sell party against the master list; return canonical name."""
    name = (raw or "").strip()
    if not name:
        raise ValueError("Sell party is required.")
    parties = list_sell_parties(include_archived=False, status="Active")
    for party in parties:
        if (party.get("name") or "").casefold() == name.casefold():
            return str(party.get("name") or name)
    raise ValueError(
        f"Sell party '{name}' is not in the Sell Party master. "
        "Add it under IPO Master → Sell Parties first."
    )


def sell_party_ids_by_name(names: list[str]) -> dict[str, str]:
    """Case-folded sell party name → id, for posting sell-side ledger entries."""
    wanted = {(n or "").strip().casefold() for n in names if n and n.strip()}
    if not wanted:
        return {}
    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{SELL_PARTIES_TABLE}",
        headers=_service_headers(),
        params={"select": "id,name", "limit": "5000"},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        return {}
    out: dict[str, str] = {}
    for row in response.json() or []:
        key = (row.get("name") or "").strip().casefold()
        if key in wanted and row.get("id"):
            out[key] = str(row["id"])
    return out


def archive_sell_party(sell_party_id: str) -> dict[str, Any]:
    return update_sell_party(
        sell_party_id, SellPartyUpdate(is_archived=True, status="Inactive")
    )


def delete_sell_party(sell_party_id: str) -> None:
    existing = get_sell_party(sell_party_id)
    trade_count = int(existing.get("trade_count") or 0)
    if trade_count > 0:
        raise ValueError(
            f"Cannot delete sell party '{existing.get('name')}': linked to {trade_count} sell(s). "
            "Archive it instead."
        )
    response = httpx.delete(
        f"{_supabase_url()}/rest/v1/{SELL_PARTIES_TABLE}",
        headers=_service_headers(),
        params={"id": f"eq.{sell_party_id}"},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(
            f"Delete sell party failed ({response.status_code}): {response.text[:300]}"
        )


def _sell_trade_counts(_ids: list[str], names: list[str]) -> dict[str, int]:
    """Count sells by sell_party name (case-insensitive)."""
    wanted = {(n or "").casefold() for n in names if n}
    if not wanted:
        return {}
    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{SELLS_TABLE}",
        headers=_service_headers(),
        params={"select": "sell_party", "limit": "10000"},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        return {}
    data = response.json()
    counts: dict[str, int] = {}
    for row in data if isinstance(data, list) else []:
        key = (row.get("sell_party") or "").casefold()
        if key in wanted:
            counts[key] = counts.get(key, 0) + 1
    return counts
