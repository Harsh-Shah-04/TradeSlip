from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


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
    amount_bhni: float | None = Field(default=None, ge=0)
    amount_shni: float | None = Field(default=None, ge=0)
    amount_retail_15k: float | None = Field(default=None, ge=0)
    amount_retail_2minus: float | None = Field(default=None, ge=0)
    amount_shareholder_15k: float | None = Field(default=None, ge=0)
    amount_shareholder_2minus: float | None = Field(default=None, ge=0)
    listing_price: float | None = Field(default=None, ge=0)

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
    amount_bhni: float | None = Field(default=None, ge=0)
    amount_shni: float | None = Field(default=None, ge=0)
    amount_retail_15k: float | None = Field(default=None, ge=0)
    amount_retail_2minus: float | None = Field(default=None, ge=0)
    amount_shareholder_15k: float | None = Field(default=None, ge=0)
    amount_shareholder_2minus: float | None = Field(default=None, ge=0)
    listing_price: float | None = Field(default=None, ge=0)
    clear_listing_price: bool = False
    clear_amount_bhni: bool = False
    clear_amount_shni: bool = False
    clear_amount_retail_15k: bool = False
    clear_amount_retail_2minus: bool = False
    clear_amount_shareholder_15k: bool = False
    clear_amount_shareholder_2minus: bool = False
    is_archived: bool | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in ("Upcoming", "Active", "Closed"):
            raise ValueError("status must be Upcoming, Active, or Closed")
        return value


class AllotmentUpdate(BaseModel):
    status: str | None = None
    shares_allotted: int | None = Field(default=None, ge=0)
    listing_price_override: float | None = Field(default=None, ge=0)
    clear_listing_price_override: bool = False
    notes: str | None = Field(default=None, max_length=2000)


class AllotmentBulkItem(BaseModel):
    id: str = Field(min_length=1)
    status: str | None = None
    shares_allotted: int | None = Field(default=None, ge=0)
    listing_price_override: float | None = Field(default=None, ge=0)
    clear_listing_price_override: bool = False


class AllotmentBulkUpdate(BaseModel):
    updates: list[AllotmentBulkItem] = Field(min_length=1)


class AllotmentSeedRequest(BaseModel):
    ipo_id: str = Field(min_length=1)


class CommonListingPriceRequest(BaseModel):
    ipo_id: str = Field(min_length=1)
    listing_price: float | None = Field(default=None, ge=0)
    clear_listing_price: bool = False


class MarkSoldRequest(BaseModel):
    ipo_id: str = Field(min_length=1)
    sell_date: str = Field(min_length=10, max_length=10)


class MarkSoldSelectedRequest(BaseModel):
    allotment_ids: list[str] = Field(min_length=1)
    sell_date: str = Field(min_length=10, max_length=10)


class UnmarkSoldSelectedRequest(BaseModel):
    allotment_ids: list[str] = Field(min_length=1)


class MarkSoldRowRequest(BaseModel):
    sell_date: str = Field(min_length=10, max_length=10)


class SettlementCreateRequest(BaseModel):
    ipo_id: str = Field(min_length=1)
    notes: str = Field(default="", max_length=2000)


class LedgerPaymentRequest(BaseModel):
    party_id: str = Field(min_length=1)
    entry_type: str = Field(min_length=1)
    amount: float = Field(gt=0)
    entry_date: str = Field(min_length=10, max_length=10)
    notes: str = Field(default="", max_length=2000)
    ipo_id: str | None = None

    @field_validator("entry_type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        if value not in ("PaymentReceived", "PaymentPaid"):
            raise ValueError("entry_type must be PaymentReceived or PaymentPaid")
        return value


class PartyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    notes: str = Field(default="", max_length=2000)
    status: str = Field(default="Active")

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in ("Active", "Inactive"):
            raise ValueError("status must be Active or Inactive")
        return value

    @field_validator("name", "notes")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return _strip(value)


class PartyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    status: str | None = None
    is_archived: bool | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in ("Active", "Inactive"):
            raise ValueError("status must be Active or Inactive")
        return value


class SellPartyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    notes: str = Field(default="", max_length=2000)
    status: str = Field(default="Active")

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in ("Active", "Inactive"):
            raise ValueError("status must be Active or Inactive")
        return value

    @field_validator("name", "notes")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return _strip(value)


class SellPartyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    status: str | None = None
    is_archived: bool | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in ("Active", "Inactive"):
            raise ValueError("status must be Active or Inactive")
        return value


class ApplicantCreate(BaseModel):
    party_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=500)
    pan: str = Field(default="", max_length=20)
    dpid: str = Field(default="", max_length=50)
    category: str = Field(default="", max_length=100)
    default_app_amount: float | None = None
    mobile: str = Field(default="", max_length=30)
    email: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=2000)
    status: str = Field(default="Active")

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in ("Active", "Inactive"):
            raise ValueError("status must be Active or Inactive")
        return value

    @field_validator("name", "pan", "dpid", "category", "mobile", "email", "notes")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return _strip(value)


class ApplicantUpdate(BaseModel):
    party_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=500)
    pan: str | None = Field(default=None, max_length=20)
    dpid: str | None = Field(default=None, max_length=50)
    category: str | None = Field(default=None, max_length=100)
    default_app_amount: float | None = None
    clear_default_app_amount: bool = False
    mobile: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    status: str | None = None
    is_archived: bool | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in ("Active", "Inactive"):
            raise ValueError("status must be Active or Inactive")
        return value


class PositionCreate(BaseModel):
    """Buy stage — party + quantity. Optional sell can be recorded in the same request."""

    ipo_id: str = Field(min_length=1)
    trade_date: str = Field(min_length=10, max_length=10)
    party_id: str = Field(min_length=1)
    category: str = Field(min_length=1, max_length=100)
    sub_category: str = Field(default="", max_length=100)
    buy_app: float = Field(gt=0)
    buy_rate: float = Field(ge=0)
    # Optional immediate sell (Workflow 1)
    include_sell: bool = False
    sell_date: str | None = Field(default=None, min_length=10, max_length=10)
    sell_party: str | None = Field(default=None, max_length=200)
    sell_app: float | None = Field(default=None, gt=0)
    sell_rate: float | None = Field(default=None, ge=0)

    @field_validator("category", "sub_category")
    @classmethod
    def strip_cat(cls, value: str) -> str:
        return _strip(value)

    @field_validator("sell_party")
    @classmethod
    def strip_sell_party(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _strip(value)

    @model_validator(mode="after")
    def validate_optional_sell(self) -> PositionCreate:
        from utils.ipo.categories import validate_category_pair

        cat, sub = validate_category_pair(self.category, self.sub_category)
        object.__setattr__(self, "category", cat)
        object.__setattr__(self, "sub_category", sub)

        if not self.include_sell:
            return self
        missing: list[str] = []
        if not (self.sell_party or "").strip():
            missing.append("Sell Party")
        if self.sell_app is None:
            missing.append("Sell Applications" if cat != "Premium" else "Sell Shares")
        if self.sell_rate is None:
            missing.append("Sell Rate")
        if missing:
            raise ValueError(
                "Sell details incomplete. Provide "
                + ", ".join(missing)
                + ", or turn off Add Sell Details."
            )
        if not self.sell_date:
            self.sell_date = self.trade_date
        if float(self.sell_app or 0) > float(self.buy_app) + 1e-9:
            qty = "shares" if cat == "Premium" else "applications"
            raise ValueError(
                f"Sell {qty} ({self.sell_app}) cannot exceed buy {qty} ({self.buy_app})."
            )
        return self


class PositionUpdate(BaseModel):
    trade_date: str | None = Field(default=None, min_length=10, max_length=10)
    party_id: str | None = None
    category: str | None = Field(default=None, min_length=1, max_length=100)
    sub_category: str | None = Field(default=None, max_length=100)
    buy_app: float | None = Field(default=None, gt=0)
    buy_rate: float | None = Field(default=None, ge=0)
    ipo_id: str | None = None


class SellCreate(BaseModel):
    sell_date: str = Field(min_length=10, max_length=10)
    sell_app: float = Field(gt=0)
    sell_rate: float = Field(ge=0)
    sell_party: str = Field(min_length=1, max_length=200)
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
    notes: str | None = Field(default=None, max_length=1000)


class AllocationSet(BaseModel):
    """Replace all applicants allocated to a buy position."""

    applicant_ids: list[str] = Field(min_length=1)

    @field_validator("applicant_ids")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        cleaned = [v.strip() for v in value if v and str(v).strip()]
        if not cleaned:
            raise ValueError("Select at least one applicant.")
        return list(dict.fromkeys(cleaned))



def _iso(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
        "amount_bhni": _optional_float(row.get("amount_bhni")),
        "amount_shni": _optional_float(row.get("amount_shni")),
        "amount_retail_15k": _optional_float(row.get("amount_retail_15k")),
        "amount_retail_2minus": _optional_float(row.get("amount_retail_2minus")),
        "amount_shareholder_15k": _optional_float(row.get("amount_shareholder_15k")),
        "amount_shareholder_2minus": _optional_float(row.get("amount_shareholder_2minus")),
        "listing_price": _optional_float(row.get("listing_price")),
        "is_archived": bool(row.get("is_archived", False)),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }
    if trade_count is not None:
        payload["trade_count"] = trade_count
    return payload


def party_to_json(row: dict[str, Any], *, applicant_count: int | None = None) -> dict[str, Any]:
    payload = {
        "id": str(row.get("id") or ""),
        "name": row.get("name"),
        "notes": row.get("notes") or "",
        "status": row.get("status") or "Active",
        "is_archived": bool(row.get("is_archived", False)),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }
    if applicant_count is not None:
        payload["applicant_count"] = applicant_count
    return payload


def sell_party_to_json(row: dict[str, Any], *, trade_count: int | None = None) -> dict[str, Any]:
    payload = {
        "id": str(row.get("id") or ""),
        "name": row.get("name"),
        "notes": row.get("notes") or "",
        "status": row.get("status") or "Active",
        "is_archived": bool(row.get("is_archived", False)),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }
    if trade_count is not None:
        payload["trade_count"] = trade_count
    return payload


def applicant_to_json(
    row: dict[str, Any], *, party: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "party_id": str(row.get("party_id") or ""),
        "party": party,
        "name": row.get("name"),
        "pan": row.get("pan") or "",
        "dpid": row.get("dpid") or "",
        "category": row.get("category") or "",
        "default_app_amount": (
            None
            if row.get("default_app_amount") is None
            else float(row.get("default_app_amount"))
        ),
        "mobile": row.get("mobile") or "",
        "email": row.get("email") or "",
        "notes": row.get("notes") or "",
        "status": row.get("status") or "Active",
        "is_archived": bool(row.get("is_archived", False)),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def sell_to_json(row: dict[str, Any]) -> dict[str, Any]:
    # Prefer brokerage; fall back to legacy dalal if migration not applied yet
    brokerage_raw = row.get("brokerage")
    if brokerage_raw is None:
        brokerage_raw = row.get("dalal")
    return {
        "id": str(row.get("id") or ""),
        "broker_id": str(row.get("broker_id") or ""),
        "position_id": str(row.get("position_id") or ""),
        "sell_date": _iso(row.get("sell_date")),
        "sell_app": float(row.get("sell_app") or 0),
        "sell_rate": float(row.get("sell_rate") or 0),
        "sell_amt": float(row.get("sell_amt") or 0),
        "sell_party": row.get("sell_party"),
        "brokerage": None if brokerage_raw is None else float(brokerage_raw),
        "notes": row.get("notes") or "",
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def allotment_to_json(row: dict[str, Any]) -> dict[str, Any]:
    applicant = row.get("ipo_applicants")
    party = None
    applicant_out = None
    if isinstance(applicant, dict):
        party_raw = applicant.get("ipo_parties")
        if isinstance(party_raw, dict):
            party = {
                "id": str(party_raw.get("id") or ""),
                "name": party_raw.get("name") or "",
            }
        applicant_out = {
            "id": str(applicant.get("id") or row.get("applicant_id") or ""),
            "name": applicant.get("name") or "",
            "pan": applicant.get("pan") or "",
            "dpid": applicant.get("dpid") or "",
            "party_id": str(applicant.get("party_id") or (party or {}).get("id") or ""),
            "party": party,
        }
    orphaned = row.get("position_id") is None
    return {
        "id": str(row.get("id") or ""),
        "ipo_id": str(row.get("ipo_id") or ""),
        "position_id": None if row.get("position_id") is None else str(row.get("position_id")),
        "applicant_id": str(row.get("applicant_id") or ""),
        "broker_id": str(row.get("broker_id") or ""),
        "sub_category": row.get("sub_category") or "",
        "cost_per_app": _optional_float(row.get("cost_per_app")),
        "status": row.get("status") or "Pending",
        "shares_allotted": int(row.get("shares_allotted") or 0),
        "listing_price_override": _optional_float(row.get("listing_price_override")),
        "is_sold": bool(row.get("is_sold", False)),
        "sold_price": _optional_float(row.get("sold_price")),
        "sold_at": _iso(row.get("sold_at")),
        "is_archived": bool(row.get("is_archived", False)),
        "orphaned": orphaned,
        "notes": row.get("notes") or "",
        "applicant": applicant_out,
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def settlement_line_to_json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "settlement_id": str(row.get("settlement_id") or ""),
        "allotment_id": None if row.get("allotment_id") is None else str(row.get("allotment_id")),
        "party_id": None if row.get("party_id") is None else str(row.get("party_id")),
        "applicant_id": None if row.get("applicant_id") is None else str(row.get("applicant_id")),
        "position_id": None if row.get("position_id") is None else str(row.get("position_id")),
        "party_name": row.get("party_name") or "",
        "applicant_name": row.get("applicant_name") or "",
        "pan": row.get("pan") or "",
        "dpid": row.get("dpid") or "",
        "sub_category": row.get("sub_category") or "",
        "application_amount": _optional_float(row.get("application_amount")),
        "vyaj": float(row.get("vyaj") or 0),
        "applied": float(row.get("applied") or 0),
        "allotted_apps": float(row.get("allotted_apps") or 0),
        "shares_allotted": int(row.get("shares_allotted") or 0),
        "sell_premium": float(row.get("sell_premium") or 0),
        "sell_amt": float(row.get("sell_amt") or 0),
        "net_pl": float(row.get("net_pl") or 0),
        "direction": row.get("direction") or "Settled",
    }


def settlement_to_json(
    row: dict[str, Any], *, lines: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    payload = {
        "id": str(row.get("id") or ""),
        "ipo_id": str(row.get("ipo_id") or ""),
        "broker_id": str(row.get("broker_id") or ""),
        "status": row.get("status") or "Draft",
        "listing_price_used": _optional_float(row.get("listing_price_used")),
        "notes": row.get("notes") or "",
        "finalized_at": _iso(row.get("finalized_at")),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }
    if lines is not None:
        payload["lines"] = lines
    return payload


def ledger_entry_to_json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "party_id": str(row.get("party_id") or ""),
        "ipo_id": None if row.get("ipo_id") is None else str(row.get("ipo_id")),
        "entry_type": row.get("entry_type"),
        "amount": float(row.get("amount") or 0),
        "balance_after": float(row.get("balance_after") or 0),
        "reference_type": row.get("reference_type") or "",
        "reference_id": None if row.get("reference_id") is None else str(row.get("reference_id")),
        "entry_date": _iso(row.get("entry_date")),
        "notes": row.get("notes") or "",
        "created_at": _iso(row.get("created_at")),
    }


def compute_position_status(buy_app: float, sold_app: float) -> str:
    if sold_app <= 0:
        return "Open"
    if sold_app + 1e-9 < buy_app:
        return "Partially Sold"
    return "Closed"


def compute_allocation_status(buy_app: float, allocated_count: int) -> str:
    required = int(round(buy_app))
    if abs(buy_app - required) > 1e-9:
        # Non-integer BUY APP — treat as unallocated until corrected
        return "Unallocated" if allocated_count == 0 else "Partial"
    if allocated_count <= 0:
        return "Unallocated"
    if allocated_count == required:
        return "Fully Allocated"
    return "Partial"


def position_to_json(
    row: dict[str, Any],
    *,
    sold_app: float = 0.0,
    sells: list[dict[str, Any]] | None = None,
    ipo: dict[str, Any] | None = None,
    allocations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from utils.ipo.categories import is_premium, quantity_label, sell_quantity_label

    buy_app = float(row.get("buy_app") or 0)
    remaining = max(buy_app - sold_app, 0.0)
    allocs = allocations or []
    category = row.get("category") or ""
    return {
        "id": str(row.get("id") or ""),
        "broker_id": str(row.get("broker_id") or ""),
        "ipo_id": str(row.get("ipo_id") or ""),
        "ipo": ipo,
        "trade_date": _iso(row.get("trade_date")),
        "party_id": str(row.get("party_id") or "") or None,
        "party": row.get("party"),
        "category": category,
        "sub_category": row.get("sub_category") or "",
        "category_group": row.get("category_group"),
        "is_premium": is_premium(category),
        "quantity_label": quantity_label(category),
        "sell_quantity_label": sell_quantity_label(category),
        "applicant_name": row.get("applicant_name") or "",
        "buy_app": buy_app,
        "buy_rate": float(row.get("buy_rate") or 0),
        "buy_amt": float(row.get("buy_amt") or 0),
        "sold_app": sold_app,
        "remaining_app": remaining,
        "status": compute_position_status(buy_app, sold_app),
        "allocations": allocs,
        "allocated_count": len(allocs),
        "allocation_status": compute_allocation_status(buy_app, len(allocs)),
        "sells": sells or [],
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }
