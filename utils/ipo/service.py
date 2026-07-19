from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import httpx

from utils.ipo.categories import category_group_for, normalize_mail
from utils.ipo.models import IpoTradeCreate, IpoTradeUpdate, trade_to_json
from utils.supabase_client import _service_headers, _supabase_url

TRADES_TABLE = "ipo_trades"
LABELS_TABLE = "ipo_category_labels"
HTTP_TIMEOUT = 30.0
MONEY_QUANT = Decimal("0.0001")


def _money(value: float | int | Decimal | str) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def calc_amounts(buy_app: float, buy_rate: float, sell_app: float, sell_rate: float) -> tuple[Decimal, Decimal]:
    buy_amt = (_money(buy_app) * _money(buy_rate)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    sell_amt = (_money(sell_app) * _money(sell_rate)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    return buy_amt, sell_amt


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
        # Fall back to in-code seeds if table missing during first deploy
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


def list_trades(
    broker_id: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    script: str | None = None,
    party: str | None = None,
    category: str | None = None,
    sell_party: str | None = None,
    mail: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, str] = {
        "select": "*",
        "broker_id": f"eq.{broker_id}",
        "order": "trade_date.desc,created_at.desc",
        "limit": "5000",
    }
    and_parts: list[str] = []
    if date_from and date_to:
        and_parts.append(f"trade_date.gte.{date_from}")
        and_parts.append(f"trade_date.lte.{date_to}")
    elif date_from:
        params["trade_date"] = f"gte.{date_from}"
    elif date_to:
        params["trade_date"] = f"lte.{date_to}"
    if and_parts:
        params["and"] = f"({','.join(and_parts)})"

    if script and script.strip():
        params["script"] = f"ilike.*{script.strip()}*"
    if party and party.strip():
        params["party"] = f"ilike.*{party.strip()}*"
    if category and category.strip():
        params["category"] = f"ilike.*{category.strip()}*"
    if sell_party and sell_party.strip():
        params["sell_party"] = f"ilike.*{sell_party.strip()}*"
    if mail and mail.strip():
        params["mail"] = f"eq.{normalize_mail(mail)}"

    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{TRADES_TABLE}",
        headers=_service_headers(),
        params=params,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"List IPO trades failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    rows = data if isinstance(data, list) else []
    return [trade_to_json(row) for row in rows]


def get_trade(broker_id: str, trade_id: str) -> dict[str, Any]:
    response = httpx.get(
        f"{_supabase_url()}/rest/v1/{TRADES_TABLE}",
        headers=_service_headers(),
        params={
            "select": "*",
            "id": f"eq.{trade_id}",
            "broker_id": f"eq.{broker_id}",
            "limit": "1",
        },
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Fetch IPO trade failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    if isinstance(data, list) and data:
        return trade_to_json(data[0])
    raise LookupError("IPO trade not found.")


def create_trade(broker_id: str, payload: IpoTradeCreate) -> dict[str, Any]:
    buy_amt, sell_amt = calc_amounts(
        payload.buy_app, payload.buy_rate, payload.sell_app, payload.sell_rate
    )
    category = payload.category.strip()
    row = {
        "broker_id": broker_id,
        "trade_date": payload.trade_date,
        "script": payload.script.strip(),
        "party": payload.party.strip(),
        "category": category,
        "category_group": category_group_for(category),
        "buy_app": float(_money(payload.buy_app)),
        "buy_rate": float(_money(payload.buy_rate)),
        "buy_amt": float(buy_amt),
        "dalal": None if payload.dalal is None else float(_money(payload.dalal)),
        "sell_app": float(_money(payload.sell_app)),
        "sell_rate": float(_money(payload.sell_rate)),
        "sell_amt": float(sell_amt),
        "sell_party": payload.sell_party.strip(),
        "applicant_name": (payload.applicant_name or "").strip(),
        "mail": normalize_mail(payload.mail),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    response = httpx.post(
        f"{_supabase_url()}/rest/v1/{TRADES_TABLE}",
        headers=_service_headers(
            {
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            }
        ),
        json=row,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Create IPO trade failed ({response.status_code}): {response.text[:300]}")
    data = response.json()
    if isinstance(data, list) and data:
        return trade_to_json(data[0])
    if isinstance(data, dict) and data:
        return trade_to_json(data)
    raise RuntimeError("Create IPO trade returned empty response.")


def update_trade(broker_id: str, trade_id: str, payload: IpoTradeUpdate) -> dict[str, Any]:
    existing = get_trade(broker_id, trade_id)
    trade_date = payload.trade_date or existing["trade_date"]
    script = payload.script if payload.script is not None else existing["script"]
    party = payload.party if payload.party is not None else existing["party"]
    category = payload.category if payload.category is not None else existing["category"]
    buy_app = payload.buy_app if payload.buy_app is not None else existing["buy_app"]
    buy_rate = payload.buy_rate if payload.buy_rate is not None else existing["buy_rate"]
    sell_app = payload.sell_app if payload.sell_app is not None else existing["sell_app"]
    sell_rate = payload.sell_rate if payload.sell_rate is not None else existing["sell_rate"]
    sell_party = payload.sell_party if payload.sell_party is not None else existing["sell_party"]
    applicant_name = (
        payload.applicant_name if payload.applicant_name is not None else existing["applicant_name"]
    )
    mail = payload.mail if payload.mail is not None else existing["mail"]

    if payload.clear_dalal:
        dalal: float | None = None
    elif payload.dalal is not None:
        dalal = float(_money(payload.dalal))
    else:
        dalal = existing["dalal"]

    buy_amt, sell_amt = calc_amounts(buy_app, buy_rate, sell_app, sell_rate)
    category = str(category).strip()
    patch = {
        "trade_date": trade_date,
        "script": str(script).strip(),
        "party": str(party).strip(),
        "category": category,
        "category_group": category_group_for(category),
        "buy_app": float(_money(buy_app)),
        "buy_rate": float(_money(buy_rate)),
        "buy_amt": float(buy_amt),
        "dalal": dalal,
        "sell_app": float(_money(sell_app)),
        "sell_rate": float(_money(sell_rate)),
        "sell_amt": float(sell_amt),
        "sell_party": str(sell_party).strip(),
        "applicant_name": str(applicant_name or "").strip(),
        "mail": normalize_mail(str(mail)),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    response = httpx.patch(
        f"{_supabase_url()}/rest/v1/{TRADES_TABLE}",
        headers=_service_headers(
            {
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            }
        ),
        params={"id": f"eq.{trade_id}", "broker_id": f"eq.{broker_id}"},
        json=patch,
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Update IPO trade failed ({response.status_code}): {response.text[:300]}")
    data = response.json() if response.content else []
    if isinstance(data, list) and data:
        return trade_to_json(data[0])
    return get_trade(broker_id, trade_id)


def delete_trade(broker_id: str, trade_id: str) -> None:
    # Ensure ownership
    get_trade(broker_id, trade_id)
    response = httpx.delete(
        f"{_supabase_url()}/rest/v1/{TRADES_TABLE}",
        headers=_service_headers({"Prefer": "return=minimal"}),
        params={"id": f"eq.{trade_id}", "broker_id": f"eq.{broker_id}"},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Delete IPO trade failed ({response.status_code}): {response.text[:300]}")


def _parse_excel_date(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("DATE is required")
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        datetime.strptime(raw, "%Y-%m-%d")
        return raw
    # DD.MM.YY or DD.MM.YYYY
    m = re.match(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})$", raw)
    if not m:
        raise ValueError(f"Unrecognized DATE: {raw!r}")
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    return datetime(year, month, day).date().isoformat()


def _num(value: str | None, field: str) -> float:
    raw = (value or "").strip().replace(",", "")
    if raw == "" or raw.upper() == "NA":
        raise ValueError(f"{field} is required")
    return float(raw)


def _optional_num(value: str | None) -> float | None:
    raw = (value or "").strip().replace(",", "")
    if raw == "" or raw.upper() == "NA":
        return None
    return float(raw)


def _header_map(headers: list[str]) -> dict[str, str]:
    aliases = {
        "date": "trade_date",
        "script": "script",
        "party": "party",
        "category": "category",
        "catagry": "category",
        "catagory": "category",
        "buy app": "buy_app",
        "buy_app": "buy_app",
        "buy rate": "buy_rate",
        "buy_rate": "buy_rate",
        "buy amt": "buy_amt",
        "buy_amt": "buy_amt",
        "dalal": "dalal",
        "sell app": "sell_app",
        "sell_app": "sell_app",
        "sell rate": "sell_rate",
        "sell_rate": "sell_rate",
        "sell amt": "sell_amt",
        "sell_amt": "sell_amt",
        "sell party": "sell_party",
        "s party": "sell_party",
        "s_party": "sell_party",
        "applicant name": "applicant_name",
        "applicant_name": "applicant_name",
        "mail": "mail",
    }
    mapping: dict[str, str] = {}
    for header in headers:
        key = re.sub(r"\s+", " ", (header or "").strip().casefold())
        if key in aliases:
            mapping[aliases[key]] = header
    return mapping


def import_trades_csv(broker_id: str, file_bytes: bytes) -> dict[str, Any]:
    text = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV has no header row.")
    colmap = _header_map(list(reader.fieldnames))
    required = [
        "trade_date",
        "script",
        "party",
        "category",
        "buy_app",
        "buy_rate",
        "sell_app",
        "sell_rate",
        "sell_party",
    ]
    missing = [name for name in required if name not in colmap]
    if missing:
        raise ValueError(f"CSV missing columns: {', '.join(missing)}")

    created = 0
    failed: list[dict[str, str]] = []
    for idx, row in enumerate(reader, start=2):
        try:
            payload = IpoTradeCreate(
                trade_date=_parse_excel_date(row.get(colmap["trade_date"], "")),
                script=row.get(colmap["script"], ""),
                party=row.get(colmap["party"], ""),
                category=row.get(colmap["category"], ""),
                buy_app=_num(row.get(colmap["buy_app"], ""), "BUY APP"),
                buy_rate=_num(row.get(colmap["buy_rate"], ""), "BUY RATE"),
                dalal=_optional_num(row.get(colmap["dalal"], "")) if "dalal" in colmap else None,
                sell_app=_num(row.get(colmap["sell_app"], ""), "SELL APP"),
                sell_rate=_num(row.get(colmap["sell_rate"], ""), "SELL RATE"),
                sell_party=row.get(colmap["sell_party"], ""),
                applicant_name=row.get(colmap["applicant_name"], "") if "applicant_name" in colmap else "",
                mail=row.get(colmap["mail"], "Pending") if "mail" in colmap else "Pending",
            )
            create_trade(broker_id, payload)
            created += 1
        except Exception as exc:  # noqa: BLE001 — collect per-row import errors
            failed.append({"row": str(idx), "error": str(exc)})

    return {"created": created, "failed_count": len(failed), "failed": failed[:50]}
