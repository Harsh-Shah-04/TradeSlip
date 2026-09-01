"""Lightweight storage path helpers (no PDF/reportlab dependencies)."""

from __future__ import annotations

import re
from datetime import datetime


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("._") or "UNKNOWN"


def parse_trade_date_partitions(trade_date_iso: str) -> tuple[str, str, str]:
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", trade_date_iso.strip())
    if not match:
        raise ValueError(f"trade_date must be YYYY-MM-DD, received {trade_date_iso!r}.")
    year, month, day = match.groups()
    datetime.strptime(f"{year}-{month}-{day}", "%Y-%m-%d")
    return year, month, day


def storage_path_for(
    client_code: str,
    trade_date_iso: str,
    broker_id: str | None = None,
) -> str:
    year, month, day = parse_trade_date_partitions(trade_date_iso)
    safe_code = safe_filename(client_code)
    filename = f"{safe_code}_{trade_date_iso}.pdf"
    if broker_id:
        return f"{broker_id.strip()}/{year}/{month}/{day}/{filename}"
    return f"{year}/{month}/{day}/{filename}"
