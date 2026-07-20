"""Phase 3 — Settlement preview, draft, finalize, Excel export."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from typing import Any

import httpx
from openpyxl import Workbook

from utils.ipo.allotments import list_allotments_raw
from utils.ipo.categories import application_amount_from_ipo
from utils.ipo.ledger import post_settlement_entries
from utils.ipo.models import (
    settlement_line_to_json,
    settlement_to_json,
)
from utils.ipo.service import get_ipo
from utils.supabase_client import _service_headers, _supabase_url

SETTLEMENTS_TABLE = "ipo_settlements"
LINES_TABLE = "ipo_settlement_lines"
HTTP_TIMEOUT = 30.0
MONEY_QUANT = Decimal("0.0001")

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


def _direction(net_pl: float) -> str:
    if abs(net_pl) < 1e-9:
        return "Settled"
    return "Receivable" if net_pl > 0 else "Payable"


def _build_line_from_allotment(row: dict[str, Any], ipo: dict[str, Any]) -> dict[str, Any]:
    applicant = row.get("ipo_applicants") if isinstance(row.get("ipo_applicants"), dict) else {}
    party = applicant.get("ipo_parties") if isinstance(applicant.get("ipo_parties"), dict) else {}
    status = row.get("status") or "Pending"
    shares = int(row.get("shares_allotted") or 0) if status == "Allotted" else 0
    allotted_apps = 1.0 if status == "Allotted" else 0.0
    vyaj = float(_money(row.get("cost_per_app")))
    sell_premium = float(_money(row.get("sold_price"))) if row.get("is_sold") else 0.0
    sell_amt = float(_money(shares) * _money(sell_premium))
    net_pl = float(_money(sell_amt) - _money(vyaj))
    sub = row.get("sub_category") or ""
    return {
        "allotment_id": str(row.get("id") or ""),
        "party_id": str(applicant.get("party_id") or party.get("id") or "") or None,
        "applicant_id": str(row.get("applicant_id") or "") or None,
        "position_id": None if row.get("position_id") is None else str(row.get("position_id")),
        "party_name": party.get("name") or "",
        "applicant_name": applicant.get("name") or "",
        "pan": applicant.get("pan") or "",
        "dpid": applicant.get("dpid") or "",
        "sub_category": sub,
        "application_amount": application_amount_from_ipo(ipo, sub),
        "vyaj": vyaj,
        "applied": 1.0,
        "allotted_apps": allotted_apps,
        "shares_allotted": shares,
        "sell_premium": sell_premium,
        "sell_amt": sell_amt,
        "net_pl": net_pl,
        "direction": _direction(net_pl),
        "status": status,
        "is_sold": bool(row.get("is_sold")),
    }


def _party_summary(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_party: dict[str, dict[str, Any]] = {}
    for line in lines:
        key = line.get("party_id") or line.get("party_name") or "unknown"
        bucket = by_party.setdefault(
            key,
            {
                "party_id": line.get("party_id"),
                "party_name": line.get("party_name") or "—",
                "applied": 0.0,
                "allotted": 0.0,
                "shares_allotted": 0,
                "sell_amt": 0.0,
                "vyaj": 0.0,
                "net_pl": 0.0,
            },
        )
        bucket["applied"] += float(line.get("applied") or 0)
        bucket["allotted"] += float(line.get("allotted_apps") or 0)
        bucket["shares_allotted"] += int(line.get("shares_allotted") or 0)
        bucket["sell_amt"] += float(line.get("sell_amt") or 0)
        bucket["vyaj"] += float(line.get("vyaj") or 0)
        bucket["net_pl"] += float(line.get("net_pl") or 0)
    result = []
    for bucket in by_party.values():
        bucket["direction"] = _direction(bucket["net_pl"])
        # average sell premium for display when shares > 0
        if bucket["shares_allotted"] > 0:
            bucket["sell_premium"] = bucket["sell_amt"] / bucket["shares_allotted"]
        else:
            bucket["sell_premium"] = 0.0
        result.append(bucket)
    result.sort(key=lambda x: (x.get("party_name") or "").lower())
    return result


def _grey_market_brokerage(ipo_id: str) -> float:
    """Sum brokerage from grey-market sells for positions of this IPO."""
    total = 0.0
    pos_resp = _http().get(
        f"{_supabase_url()}/rest/v1/ipo_positions",
        headers=_service_headers(),
        params={"select": "id", "ipo_id": f"eq.{ipo_id}", "limit": "5000"},
        timeout=HTTP_TIMEOUT,
    )
    if pos_resp.status_code != 200:
        return 0.0
    ids = [str(r["id"]) for r in (pos_resp.json() or []) if r.get("id")]
    if not ids:
        return 0.0
    for i in range(0, len(ids), 100):
        chunk = ids[i : i + 100]
        id_filter = "(" + ",".join(chunk) + ")"
        sell_resp = _http().get(
            f"{_supabase_url()}/rest/v1/ipo_sells",
            headers=_service_headers(),
            params={"select": "brokerage", "position_id": f"in.{id_filter}", "limit": "5000"},
            timeout=HTTP_TIMEOUT,
        )
        if sell_resp.status_code != 200:
            continue
        for s in sell_resp.json() or []:
            if s.get("brokerage") is not None:
                total += float(s["brokerage"])
    return float(_money(total))


def preview_settlement(ipo_id: str) -> dict[str, Any]:
    ipo = get_ipo(ipo_id, include_trade_count=False)
    rows = list_allotments_raw(ipo_id)
    lines = [_build_line_from_allotment(r, ipo) for r in rows]
    warnings: list[str] = []
    unsold = [l for l in lines if l["status"] == "Allotted" and not l["is_sold"]]
    if unsold:
        warnings.append(f"{len(unsold)} allotted applicant(s) not yet marked sold.")
    pending = [l for l in lines if l["status"] == "Pending"]
    if pending:
        warnings.append(f"{len(pending)} allotment(s) still Pending.")
    party = _party_summary(lines)
    brokerage = _grey_market_brokerage(ipo_id)
    return {
        "ipo": ipo,
        "lines": lines,
        "party_summary": party,
        "grey_market_brokerage": brokerage,
        "totals": {
            "applied": sum(l["applied"] for l in lines),
            "allotted": sum(l["allotted_apps"] for l in lines),
            "shares_allotted": sum(l["shares_allotted"] for l in lines),
            "sell_amt": sum(l["sell_amt"] for l in lines),
            "vyaj": sum(l["vyaj"] for l in lines),
            "net_pl": sum(l["net_pl"] for l in lines),
        },
        "warnings": warnings,
    }


def _get_settlement(settlement_id: str) -> dict[str, Any]:
    response = _http().get(
        f"{_supabase_url()}/rest/v1/{SETTLEMENTS_TABLE}",
        headers=_service_headers(),
        params={"id": f"eq.{settlement_id}", "select": "*", "limit": "1"},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Get settlement failed ({response.status_code})")
    rows = response.json()
    if not isinstance(rows, list) or not rows:
        raise LookupError("Settlement not found.")
    return rows[0]


def _list_lines(settlement_id: str) -> list[dict[str, Any]]:
    response = _http().get(
        f"{_supabase_url()}/rest/v1/{LINES_TABLE}",
        headers=_service_headers(),
        params={
            "settlement_id": f"eq.{settlement_id}",
            "select": "*",
            "order": "party_name.asc,applicant_name.asc",
            "limit": "20000",
        },
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"List settlement lines failed ({response.status_code})")
    rows = response.json()
    return rows if isinstance(rows, list) else []


def get_settlement(settlement_id: str) -> dict[str, Any]:
    row = _get_settlement(settlement_id)
    lines = [settlement_line_to_json(l) for l in _list_lines(settlement_id)]
    out = settlement_to_json(row, lines=lines)
    out["party_summary"] = _party_summary(lines)
    out["ipo"] = get_ipo(str(row["ipo_id"]), include_trade_count=False)
    return out


def create_or_refresh_draft(ipo_id: str, broker_id: str, notes: str = "") -> dict[str, Any]:
    # Reuse existing draft for this IPO if present
    existing = _http().get(
        f"{_supabase_url()}/rest/v1/{SETTLEMENTS_TABLE}",
        headers=_service_headers(),
        params={
            "ipo_id": f"eq.{ipo_id}",
            "status": "eq.Draft",
            "select": "*",
            "limit": "1",
        },
        timeout=HTTP_TIMEOUT,
    )
    preview = preview_settlement(ipo_id)
    ipo = preview["ipo"]
    draft_id = None
    if existing.status_code == 200 and isinstance(existing.json(), list) and existing.json():
        draft_id = str(existing.json()[0]["id"])
        # wipe lines
        _http().delete(
            f"{_supabase_url()}/rest/v1/{LINES_TABLE}",
            headers=_service_headers({"Prefer": "return=minimal"}),
            params={"settlement_id": f"eq.{draft_id}"},
            timeout=HTTP_TIMEOUT,
        )
        _http().patch(
            f"{_supabase_url()}/rest/v1/{SETTLEMENTS_TABLE}",
            headers=_service_headers({"Content-Type": "application/json"}),
            params={"id": f"eq.{draft_id}"},
            json={
                "listing_price_used": ipo.get("listing_price"),
                "notes": notes or "",
                "updated_at": _now(),
            },
            timeout=HTTP_TIMEOUT,
        )
    else:
        # Block if already finalized
        fin = _http().get(
            f"{_supabase_url()}/rest/v1/{SETTLEMENTS_TABLE}",
            headers=_service_headers(),
            params={
                "ipo_id": f"eq.{ipo_id}",
                "status": "eq.Finalized",
                "select": "id",
                "limit": "1",
            },
            timeout=HTTP_TIMEOUT,
        )
        if fin.status_code == 200 and isinstance(fin.json(), list) and fin.json():
            raise ValueError("This IPO already has a finalized settlement.")
        create = _http().post(
            f"{_supabase_url()}/rest/v1/{SETTLEMENTS_TABLE}",
            headers=_service_headers(
                {"Content-Type": "application/json", "Prefer": "return=representation"}
            ),
            json={
                "ipo_id": ipo_id,
                "broker_id": broker_id,
                "status": "Draft",
                "listing_price_used": ipo.get("listing_price"),
                "notes": notes or "",
                "updated_at": _now(),
            },
            timeout=HTTP_TIMEOUT,
        )
        if create.status_code not in (200, 201):
            raise RuntimeError(f"Create settlement failed ({create.status_code}): {create.text[:300]}")
        data = create.json()
        created = data[0] if isinstance(data, list) else data
        draft_id = str(created["id"])

    line_rows = []
    for line in preview["lines"]:
        line_rows.append(
            {
                "settlement_id": draft_id,
                "allotment_id": line.get("allotment_id"),
                "party_id": line.get("party_id"),
                "applicant_id": line.get("applicant_id"),
                "position_id": line.get("position_id"),
                "party_name": line.get("party_name") or "",
                "applicant_name": line.get("applicant_name") or "",
                "pan": line.get("pan") or "",
                "dpid": line.get("dpid") or "",
                "sub_category": line.get("sub_category") or "",
                "application_amount": line.get("application_amount"),
                "vyaj": line["vyaj"],
                "applied": line["applied"],
                "allotted_apps": line["allotted_apps"],
                "shares_allotted": line["shares_allotted"],
                "sell_premium": line["sell_premium"],
                "sell_amt": line["sell_amt"],
                "net_pl": line["net_pl"],
                "direction": line["direction"],
            }
        )
    for i in range(0, len(line_rows), 100):
        chunk = line_rows[i : i + 100]
        if not chunk:
            continue
        resp = _http().post(
            f"{_supabase_url()}/rest/v1/{LINES_TABLE}",
            headers=_service_headers(
                {"Content-Type": "application/json", "Prefer": "return=minimal"}
            ),
            json=chunk,
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Insert settlement lines failed ({resp.status_code}): {resp.text[:300]}")

    out = get_settlement(draft_id)
    out["warnings"] = preview.get("warnings") or []
    out["grey_market_brokerage"] = preview.get("grey_market_brokerage")
    return out


def finalize_settlement(settlement_id: str) -> dict[str, Any]:
    row = _get_settlement(settlement_id)
    if row.get("status") == "Finalized":
        return get_settlement(settlement_id)
    lines = _list_lines(settlement_id)
    if not lines:
        raise ValueError("Settlement has no lines. Save a draft first.")

    patch = _http().patch(
        f"{_supabase_url()}/rest/v1/{SETTLEMENTS_TABLE}",
        headers=_service_headers({"Content-Type": "application/json"}),
        params={"id": f"eq.{settlement_id}", "status": "eq.Draft"},
        json={
            "status": "Finalized",
            "finalized_at": _now(),
            "updated_at": _now(),
        },
        timeout=HTTP_TIMEOUT,
    )
    if patch.status_code not in (200, 204):
        raise RuntimeError(f"Finalize failed ({patch.status_code}): {patch.text[:300]}")

    # Post ledger: one entry per party for this settlement
    party_nets: dict[str, float] = defaultdict(float)
    party_names: dict[str, str] = {}
    for line in lines:
        pid = line.get("party_id")
        if not pid:
            continue
        party_nets[str(pid)] += float(line.get("net_pl") or 0)
        party_names[str(pid)] = line.get("party_name") or ""

    post_settlement_entries(
        ipo_id=str(row["ipo_id"]),
        settlement_id=settlement_id,
        party_nets=dict(party_nets),
    )
    return get_settlement(settlement_id)


def build_settlement_excel(settlement_id: str) -> bytes:
    data = get_settlement(settlement_id)
    lines = data.get("lines") or []
    party = data.get("party_summary") or _party_summary(lines)
    ipo = data.get("ipo") or {}
    title = ipo.get("display_name") or ipo.get("name") or "IPO"

    wb = Workbook()
    ws1 = wb.active
    ws1.title = "Party Summary"
    ws1.append(
        [
            "summary",
            "applied",
            "alloted",
            "allote share",
            "sell premium",
            "sellamt",
            "vyaj",
            "net p/l",
            "direction",
        ]
    )
    for p in party:
        ws1.append(
            [
                p.get("party_name"),
                p.get("applied"),
                p.get("allotted"),
                p.get("shares_allotted"),
                p.get("sell_premium"),
                p.get("sell_amt"),
                p.get("vyaj"),
                p.get("net_pl"),
                p.get("direction"),
            ]
        )

    ws2 = wb.create_sheet("Applicants")
    ws2.append(
        [
            "SR NO",
            "NAME",
            "DPID",
            "PAN",
            "CATEGORY",
            "AMT",
            "VYAJ",
            "applied",
            "allotted",
            "shares",
            "sell premium",
            "sellamt",
            "net p/l",
            "direction",
        ]
    )
    for i, line in enumerate(lines, start=1):
        ws2.append(
            [
                i,
                line.get("applicant_name"),
                line.get("dpid"),
                line.get("pan"),
                line.get("sub_category"),
                line.get("application_amount"),
                line.get("vyaj"),
                line.get("applied"),
                line.get("allotted_apps"),
                line.get("shares_allotted"),
                line.get("sell_premium"),
                line.get("sell_amt"),
                line.get("net_pl"),
                line.get("direction"),
            ]
        )

    ws1.cell(1, 11, title)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
