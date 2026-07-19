"""Unit tests for Phase 3 allotment / settlement helpers (no live Supabase)."""

from __future__ import annotations

import pytest

from utils.ipo.allotments import _effective_price, _money, _pair_key
from utils.ipo.models import AllotmentBulkUpdate, AllotmentUpdate, allotment_to_json
from utils.ipo.settlement import _direction, _party_summary


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
