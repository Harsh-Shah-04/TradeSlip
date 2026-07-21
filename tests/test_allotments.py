"""Unit tests for Phase 3 allotment / settlement helpers (no live Supabase)."""

from __future__ import annotations

import pytest

from utils.ipo.allotments import _effective_price, _money, _pair_key
from utils.ipo.models import AllotmentBulkUpdate, AllotmentUpdate, allotment_to_json
from utils.ipo.settlement import (
    _allotment_line_totals,
    _attribute_allotment_rows,
    _direction,
    _party_summary,
    _sell_side_financials,
    _sell_side_direction,
)


def test_pair_key():
    assert _pair_key("p1", "a1") == "p1::a1"
    assert _pair_key(None, "a1") == "::a1"


def test_effective_price_override_wins():
    row = {"listing_price_override": 91.75}
    assert _effective_price(row, 92.0) == 91.75
    assert _effective_price({"listing_price_override": None}, 92.0) == 92.0
    assert _effective_price({}, None) is None


def test_allotment_update_model():
    m = AllotmentUpdate(status="Allotted", shares_allotted=26)
    assert m.status == "Allotted"
    assert m.shares_allotted == 26


def test_allotment_bulk_update_model():
    m = AllotmentBulkUpdate(
        updates=[{"id": "a1", "status": "Not Allotted", "shares_allotted": 0}]
    )
    assert len(m.updates) == 1
    assert m.updates[0].id == "a1"


def test_allotment_to_json_orphaned():
    row = {
        "id": "x",
        "ipo_id": "i",
        "position_id": None,
        "applicant_id": "a",
        "broker_id": "b",
        "status": "Pending",
        "shares_allotted": 0,
        "is_sold": False,
        "is_archived": False,
    }
    out = allotment_to_json(row)
    assert out["orphaned"] is True
    assert out["status"] == "Pending"


def test_direction_and_party_summary():
    assert _direction(100) == "Receivable"
    assert _direction(-50) == "Payable"
    assert _direction(0) == "Settled"
    lines = [
        {
            "party_id": "p1",
            "party_name": "Dev",
            "applied": 1,
            "allotted_apps": 1,
            "shares_allotted": 26,
            "sell_amt": 2392,
            "vyaj": 1200,
            "net_pl": 1192,
        },
        {
            "party_id": "p1",
            "party_name": "Dev",
            "applied": 1,
            "allotted_apps": 0,
            "shares_allotted": 0,
            "sell_amt": 0,
            "vyaj": 1200,
            "net_pl": -1200,
        },
    ]
    summary = _party_summary(lines)
    assert len(summary) == 1
    assert summary[0]["applied"] == 2
    assert summary[0]["net_pl"] == pytest.approx(-8)
    assert summary[0]["direction"] == "Payable"


def test_money_quantize():
    assert float(_money(1.23456)) == 1.2346


def test_attribute_allotment_rows_full_position():
    rows = [
        {"applicant_id": "b", "status": "Allotted", "shares_allotted": 26, "cost_per_app": 100},
        {"applicant_id": "a", "status": "Not Allotted", "shares_allotted": 0, "cost_per_app": 100},
    ]
    out = _attribute_allotment_rows(3, 3, rows)
    assert len(out) == 2
    assert out[0]["applicant_id"] == "a"


def test_attribute_allotment_rows_partial():
    rows = [
        {"applicant_id": "a", "status": "Allotted", "shares_allotted": 10, "cost_per_app": 50},
        {"applicant_id": "b", "status": "Allotted", "shares_allotted": 20, "cost_per_app": 50},
    ]
    out = _attribute_allotment_rows(1, 2, rows)
    assert len(out) == 1
    assert out[0]["applicant_id"] == "a"


def test_allotment_line_totals_listing_side():
    rows = [
        {
            "status": "Allotted",
            "shares_allotted": 26,
            "cost_per_app": 100,
            "is_sold": True,
            "sold_price": 93,
        },
        {"status": "Not Allotted", "shares_allotted": 0, "cost_per_app": 100, "is_sold": False},
    ]
    totals = _allotment_line_totals(rows)
    assert totals["allotted"] == 1
    assert totals["shares_allotted"] == 26
    assert totals["sell_amt"] == pytest.approx(2418)
    assert totals["vyaj"] == pytest.approx(200)
    assert totals["net_pl"] == pytest.approx(2218)


def test_sell_side_direction_inverted():
    assert _sell_side_direction(1536) == "Payable"
    assert _sell_side_direction(-164) == "Receivable"
    assert _sell_side_direction(0) == "Settled"
    assert _direction(1536) == "Receivable"


def test_application_sell_side_uses_seller_vyaj_and_separate_brokerage():
    amounts = _sell_side_financials(
        listing_sell_amt=2043.6,
        seller_vyaj=3600,
        buyer_vyaj=3300,
        recorded_brokerage=300,
        premium=False,
    )
    assert amounts == {
        "sell_amt": 2043.6,
        "vyaj": 3600.0,
        "brokerage": 300.0,
        "net_pl": -1556.4,
    }


def test_premium_sell_side_is_receivable_from_sell_party():
    amounts = _sell_side_financials(
        listing_sell_amt=0,
        seller_vyaj=3990,
        buyer_vyaj=3610,
        recorded_brokerage=380,
        premium=True,
    )
    assert amounts == {
        "sell_amt": 0.0,
        "vyaj": 3990.0,
        "brokerage": 380.0,
        "net_pl": -3990.0,
    }
    assert _sell_side_direction(amounts["net_pl"]) == "Receivable"


def test_effective_price_common_over_none_override():
    assert _effective_price({"listing_price_override": None}, 92.0) == 92.0
    assert _effective_price({"listing_price_override": ""}, 92.0) == 92.0
    assert _effective_price({"listing_price_override": 91}, 92.0) == 91.0
