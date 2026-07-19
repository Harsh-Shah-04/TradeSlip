from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


def _strip(value: str) -> str:
    return value.strip()


class IpoMasterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    display_name: str = Field(default="", max_length=200)
    open_date: str | None = None
    close_date: str | None = None
    listing_date: str | None = None
    status: str = Field(default="Upcoming")
    notes: str = Field(default="", max_length=2000)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in ("Upcoming", "Active", "Closed"):
            raise ValueError("status must be Upcoming, Active, or Closed")
        return value

    @field_validator("name", "display_name", "notes")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return _strip(value)


class IpoMasterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)
    open_date: str | None = None
    close_date: str | None = None
    listing_date: str | None = None
    status: str | None = None
    notes: str | None = Field(default=None, max_length=2000)
    is_archived: bool | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in ("Upcoming", "Active", "Closed"):
            raise ValueError("status must be Upcoming, Active, or Closed")
        return value


class PositionCreate(BaseModel):
    """Buy stage only — sell happens later via sell legs."""

    ipo_id: str = Field(min_length=1)
    trade_date: str = Field(min_length=10, max_length=10)
    party: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)
    applicant_name: str = Field(default="", max_length=500)
    buy_app: float = Field(gt=0)
    buy_rate: float = Field(ge=0)

    @field_validator("party", "category", "applicant_name")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return _strip(value)


class PositionUpdate(BaseModel):
    trade_date: str | None = Field(default=None, min_length=10, max_length=10)
    party: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    applicant_name: str | None = Field(default=None, max_length=500)
    buy_app: float | None = Field(default=None, gt=0)
    buy_rate: float | None = Field(default=None, ge=0)
    ipo_id: str | None = None


class SellCreate(BaseModel):
    sell_date: str = Field(min_length=10, max_length=10)
    sell_app: float = Field(gt=0)
    sell_rate: float = Field(ge=0)
    sell_party: str = Field(min_length=1, max_length=200)
    dalal: float | None = None
    notes: str = Field(default="", max_length=1000)

    @field_validator("sell_party", "notes")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return _strip(value)


class SellUpdate(BaseModel):
    sell_date: str | None = Field(default=None, min_length=10, max_length=10)
    sell_app: float | None = Field(default=None, gt=0)
    sell_rate: float | None = Field(default=None, ge=0)
    sell_party: str | None = Field(default=None, min_length=1, max_length=200)
    dalal: float | None = None
    clear_dalal: bool = False
    notes: str | None = Field(default=None, max_length=1000)


def _iso(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def master_to_json(row: dict[str, Any], *, trade_count: int | None = None) -> dict[str, Any]:
    payload = {
        "id": str(row.get("id") or ""),
        "name": row.get("name"),
        "display_name": row.get("display_name"),
        "open_date": _iso(row.get("open_date")),
        "close_date": _iso(row.get("close_date")),
        "listing_date": _iso(row.get("listing_date")),
        "status": row.get("status"),
        "notes": row.get("notes") or "",
        "is_archived": bool(row.get("is_archived", False)),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }
    if trade_count is not None:
        payload["trade_count"] = trade_count
    return payload


def sell_to_json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "broker_id": str(row.get("broker_id") or ""),
        "position_id": str(row.get("position_id") or ""),
        "sell_date": _iso(row.get("sell_date")),
        "sell_app": float(row.get("sell_app") or 0),
        "sell_rate": float(row.get("sell_rate") or 0),
        "sell_amt": float(row.get("sell_amt") or 0),
        "sell_party": row.get("sell_party"),
        "dalal": None if row.get("dalal") is None else float(row.get("dalal")),
        "notes": row.get("notes") or "",
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def compute_position_status(buy_app: float, sold_app: float) -> str:
    if sold_app <= 0:
        return "Open"
    if sold_app + 1e-9 < buy_app:
        return "Partially Sold"
    return "Closed"


def position_to_json(
    row: dict[str, Any],
    *,
    sold_app: float = 0.0,
    sells: list[dict[str, Any]] | None = None,
    ipo: dict[str, Any] | None = None,
) -> dict[str, Any]:
    buy_app = float(row.get("buy_app") or 0)
    remaining = max(buy_app - sold_app, 0.0)
    return {
        "id": str(row.get("id") or ""),
        "broker_id": str(row.get("broker_id") or ""),
        "ipo_id": str(row.get("ipo_id") or ""),
        "ipo": ipo,
        "trade_date": _iso(row.get("trade_date")),
        "party": row.get("party"),
        "category": row.get("category"),
        "category_group": row.get("category_group"),
        "applicant_name": row.get("applicant_name") or "",
        "buy_app": buy_app,
        "buy_rate": float(row.get("buy_rate") or 0),
        "buy_amt": float(row.get("buy_amt") or 0),
        "sold_app": sold_app,
        "remaining_app": remaining,
        "status": compute_position_status(buy_app, sold_app),
        "sells": sells or [],
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }
