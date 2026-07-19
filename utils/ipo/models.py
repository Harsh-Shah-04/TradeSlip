from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class IpoTradeCreate(BaseModel):
    trade_date: str = Field(min_length=10, max_length=10)
    script: str = Field(min_length=1, max_length=200)
    party: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)
    buy_app: float = Field(ge=0)
    buy_rate: float = Field(ge=0)
    dalal: float | None = Field(default=None)
    sell_app: float = Field(ge=0)
    sell_rate: float = Field(ge=0)
    sell_party: str = Field(min_length=1, max_length=200)
    applicant_name: str = Field(default="", max_length=500)
    mail: str = Field(default="Pending", max_length=50)

    @field_validator("trade_date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        value = value.strip()
        if len(value) != 10 or value[4] != "-" or value[7] != "-":
            raise ValueError("trade_date must be YYYY-MM-DD")
        return value

    @field_validator("script", "party", "category", "sell_party", "applicant_name", "mail")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value


class IpoTradeUpdate(BaseModel):
    trade_date: str | None = Field(default=None, min_length=10, max_length=10)
    script: str | None = Field(default=None, min_length=1, max_length=200)
    party: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    buy_app: float | None = Field(default=None, ge=0)
    buy_rate: float | None = Field(default=None, ge=0)
    dalal: float | None = None
    sell_app: float | None = Field(default=None, ge=0)
    sell_rate: float | None = Field(default=None, ge=0)
    sell_party: str | None = Field(default=None, min_length=1, max_length=200)
    applicant_name: str | None = Field(default=None, max_length=500)
    mail: str | None = Field(default=None, max_length=50)
    clear_dalal: bool = False

    @field_validator("script", "party", "category", "sell_party", "applicant_name", "mail")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


def trade_to_json(row: dict[str, Any]) -> dict[str, Any]:
    def _iso(value: Any) -> Any:
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return value

    return {
        "id": str(row.get("id") or ""),
        "broker_id": str(row.get("broker_id") or ""),
        "trade_date": _iso(row.get("trade_date")),
        "script": row.get("script"),
        "party": row.get("party"),
        "category": row.get("category"),
        "category_group": row.get("category_group"),
        "buy_app": float(row.get("buy_app") or 0),
        "buy_rate": float(row.get("buy_rate") or 0),
        "buy_amt": float(row.get("buy_amt") or 0),
        "dalal": None if row.get("dalal") is None else float(row.get("dalal")),
        "sell_app": float(row.get("sell_app") or 0),
        "sell_rate": float(row.get("sell_rate") or 0),
        "sell_amt": float(row.get("sell_amt") or 0),
        "sell_party": row.get("sell_party"),
        "applicant_name": row.get("applicant_name") or "",
        "mail": row.get("mail") or "Pending",
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }
