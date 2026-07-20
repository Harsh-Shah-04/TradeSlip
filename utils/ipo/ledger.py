"""Phase 3 — Continuous Client Ledger."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from typing import Any

import httpx
from openpyxl import Workbook

from utils.ipo.models import ledger_entry_to_json
from utils.supabase_client import _service_headers, _supabase_url

LEDGER_TABLE = "ipo_ledger_entries"
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


def party_balance(party_id: str) -> float:
    response = _http().get(
        f"{_supabase_url()}/rest/v1/{LEDGER_TABLE}",
        headers=_service_headers(),
        params={
            "party_id": f"eq.{party_id}",
            "select": "balance_after,created_at",
            "order": "created_at.desc",
            "limit": "1",
        },
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Ledger balance failed ({response.status_code})")
    rows = response.json()
    if not isinstance(rows, list) or not rows:
        return 0.0
    return float(rows[0].get("balance_after") or 0)


def list_ledger(party_id: str) -> dict[str, Any]:
    response = _http().get(
        f"{_supabase_url()}/rest/v1/{LEDGER_TABLE}",
        headers=_service_headers(),
        params={
            "party_id": f"eq.{party_id}",
            "select": "*",
            "order": "created_at.asc",
            "limit": "10000",
        },
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"List ledger failed ({response.status_code}): {response.text[:300]}")
    rows = response.json() if isinstance(response.json(), list) else []
    entries = [ledger_entry_to_json(r) for r in rows]
    balance = entries[-1]["balance_after"] if entries else 0.0
    direction = "Settled"
    if balance > 1e-9:
        direction = "Receivable"
    elif balance < -1e-9:
        direction = "Payable"
    return {
        "party_id": party_id,
        "balance": balance,
        "direction": direction,
        "entries": entries,
    }


def _append_entry(
    *,
    party_id: str,
    amount: float,
    entry_type: str,
    entry_date: str,
    notes: str = "",
    ipo_id: str | None = None,
    reference_type: str = "",
    reference_id: str | None = None,
) -> dict[str, Any]:
    current = party_balance(party_id)
    new_balance = float(_money(current) + _money(amount))
    payload = {
        "party_id": party_id,
        "ipo_id": ipo_id,
        "entry_type": entry_type,
        "amount": float(_money(amount)),
        "balance_after": new_balance,
        "reference_type": reference_type,
        "reference_id": reference_id,
        "entry_date": entry_date,
        "notes": notes or "",
        "updated_at": _now(),
    }
    response = _http().post(
        f"{_supabase_url()}/rest/v1/{LEDGER_TABLE}",
        headers=_service_headers(
            {"Content-Type": "application/json", "Prefer": "return=representation"}
        ),
        json=payload,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Create ledger entry failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    row = data[0] if isinstance(data, list) else data
    return ledger_entry_to_json(row)


def post_settlement_entries(
    *,
    ipo_id: str,
    settlement_id: str,
    party_nets: dict[str, float],
) -> list[dict[str, Any]]:
    """One Settlement entry per party. amount = net_pl (+ receivable / − payable)."""
    today = datetime.now(timezone.utc).date().isoformat()
    created: list[dict[str, Any]] = []
    for party_id, net in party_nets.items():
        if abs(float(net)) < 1e-12:
            continue
        # Skip if already posted (unique index may also reject)
        check = _http().get(
            f"{_supabase_url()}/rest/v1/{LEDGER_TABLE}",
            headers=_service_headers(),
            params={
                "party_id": f"eq.{party_id}",
                "reference_id": f"eq.{settlement_id}",
                "entry_type": "eq.Settlement",
                "select": "id",
                "limit": "1",
            },
            timeout=HTTP_TIMEOUT,
        )
        if check.status_code == 200 and isinstance(check.json(), list) and check.json():
            continue
        created.append(
            _append_entry(
                party_id=party_id,
                amount=float(net),
                entry_type="Settlement",
                entry_date=today,
                notes="IPO settlement finalized",
                ipo_id=ipo_id,
                reference_type="settlement",
                reference_id=settlement_id,
            )
        )
    return created


def record_payment(
    *,
    party_id: str,
    entry_type: str,
    amount: float,
    entry_date: str,
    notes: str = "",
    ipo_id: str | None = None,
) -> dict[str, Any]:
    if entry_type not in ("PaymentReceived", "PaymentPaid"):
        raise ValueError("entry_type must be PaymentReceived or PaymentPaid")
    if amount <= 0:
        raise ValueError("amount must be > 0")
    # PaymentReceived reduces receivable (+): client paid us → amount negative to balance
    # PaymentPaid reduces payable (−): we paid client → amount positive to balance
    signed = -float(amount) if entry_type == "PaymentReceived" else float(amount)
    entry = _append_entry(
        party_id=party_id,
        amount=signed,
        entry_type=entry_type,
        entry_date=entry_date,
        notes=notes,
        ipo_id=ipo_id,
        reference_type="payment",
    )
    return list_ledger(party_id) | {"last_entry": entry}


def build_statement_excel(party_id: str, party_name: str = "") -> bytes:
    data = list_ledger(party_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "Client Ledger"
    ws.append(["Client", party_name or party_id])
    ws.append(["Outstanding", data["balance"], data["direction"]])
    ws.append([])
    ws.append(["Date", "Type", "IPO", "Amount", "Balance After", "Notes"])
    for e in data["entries"]:
        ws.append(
            [
                e.get("entry_date"),
                e.get("entry_type"),
                e.get("ipo_id") or "",
                e.get("amount"),
                e.get("balance_after"),
                e.get("notes"),
            ]
        )
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
