"""Unit tests for Phase 3 allotment / settlement helpers (no live Supabase)."""

from __future__ import annotations

import pytest

from utils.ipo.allotments import _effective_price, _money, _pair_key
from utils.ipo.models import AllotmentBulkUpdate, AllotmentUpdate, allotment_to_json
from utils.ipo.settlement import (
    _allotment_line_totals,
    _attribute_allotment_rows,
    _build_line_from_allotment,
    _direction,
    _party_summary,
    _sell_side_financials,
    _sell_side_direction,
    _subject2_allotment_totals,
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
        "market_amount": 2043.6,
        "vyaj": 3600.0,
        "settlement_difference": 300.0,
        "brokerage": 300.0,
        "profit": 300.0,
        "loss": 0.0,
        "net_pl": -1556.4,
    }


def test_premium_sell_side_uses_listing_market_and_contract():
    # PINAL: 400 shares, buy 98, Ambica sell 99, listing sold 94.
    # Contract 39,600; Market 37,600; Net = 37,600 − 39,600 = −2,000 Receivable.
    # Brokerage = 39,600 − 39,200 = 400. Client difference = 39,200 − 37,600 = 1,600.
    amounts = _sell_side_financials(
        listing_sell_amt=400 * 94,
        seller_vyaj=400 * 99,
        buyer_vyaj=400 * 98,
        recorded_brokerage=400,
        premium=True,
        client_amount=400 * 98,
    )
    assert amounts["sell_amt"] == pytest.approx(37600.0)
    assert amounts["market_amount"] == pytest.approx(37600.0)
    assert amounts["vyaj"] == pytest.approx(39600.0)
    assert amounts["settlement_difference"] == pytest.approx(1600.0)
    assert amounts["brokerage"] == pytest.approx(400.0)
    assert amounts["net_pl"] == pytest.approx(-2000.0)
    assert _sell_side_direction(amounts["net_pl"]) == "Receivable"


def test_premium_sell_side_client_pays_when_listing_above_guarantee():
    # Listing 105 > buy 98 → client difference negative (client pays).
    amounts = _sell_side_financials(
        listing_sell_amt=400 * 105,
        seller_vyaj=400 * 99,
        buyer_vyaj=400 * 98,
        recorded_brokerage=None,
        premium=True,
        client_amount=400 * 98,
    )
    assert amounts["settlement_difference"] == pytest.approx(-2800.0)
    assert amounts["brokerage"] == pytest.approx(400.0)  # 39600 − 39200
    assert amounts["net_pl"] == pytest.approx(400 * 105 - 400 * 99)  # market − contract


def test_subject2_sell_side_net_is_market_minus_contract():
    # Three independent prices: buy 84.5 (guarantee), sell rate 90 (MAMA
    # contract), sold price 39.3 (market). Same table math as other rows:
    # Net = Market − Contract = 14,305.2 − 32,760 = −18,454.8 → Receivable.
    # Reconciliation: collect 18,454.8 from MAMA, pay client 16,452.8,
    # broker keeps 2,002 = 364 × (90 − 84.5).
    amounts = _sell_side_financials(
        listing_sell_amt=364 * 39.3,  # market = 14,305.2
        seller_vyaj=364 * 90,  # contract = 32,760
        buyer_vyaj=364 * 84.5,  # guaranteed = 30,758
        recorded_brokerage=27.5,  # per-application spread must be ignored
        premium=False,
        subject2=True,
        client_amount=364 * 84.5,
    )
    assert amounts == {
        "sell_amt": 14305.2,
        "market_amount": 14305.2,
        "vyaj": 32760.0,  # contract, NOT guaranteed / market
        "settlement_difference": 16452.8,  # Guaranteed − Market (client leg)
        "brokerage": pytest.approx(2002.0),  # Contract − Guaranteed = 364 × 5.5
        "profit": pytest.approx(2002.0),
        "loss": 0.0,
        "net_pl": pytest.approx(-18454.8),
    }
    assert _sell_side_direction(amounts["net_pl"]) == "Receivable"
    # Brokerage reconciles the two legs: 18,454.8 from MAMA − 16,452.8 to client.
    client_net = -amounts["settlement_difference"]  # −16,452.8 → pay client
    assert -amounts["net_pl"] + client_net == pytest.approx(amounts["brokerage"])


def test_subject2_sell_side_excess_market_over_contract_is_payable():
    # Market 364 × 95 = 34,580 above the 32,760 contract → the 1,820 excess
    # goes to the sell party (Payable); the client leg carries the −3,822.
    amounts = _sell_side_financials(
        listing_sell_amt=364 * 95,
        seller_vyaj=364 * 90,
        buyer_vyaj=364 * 84.5,
        recorded_brokerage=None,
        premium=False,
        subject2=True,
        client_amount=364 * 84.5,
    )
    assert amounts["vyaj"] == pytest.approx(32760.0)
    assert amounts["market_amount"] == pytest.approx(34580.0)
    assert amounts["settlement_difference"] == pytest.approx(-3822.0)
    assert amounts["brokerage"] == pytest.approx(2002.0)  # Contract − Guaranteed
    assert amounts["net_pl"] == pytest.approx(1820.0)
    assert _sell_side_direction(amounts["net_pl"]) == "Payable"


def test_subject2_sell_side_zero_without_allotment():
    amounts = _sell_side_financials(
        listing_sell_amt=0,
        seller_vyaj=0,
        buyer_vyaj=0,
        recorded_brokerage=27.5,
        premium=False,
        subject2=True,
        client_amount=0,
    )
    assert amounts == {
        "sell_amt": 0.0,
        "market_amount": 0.0,
        "vyaj": 0.0,
        "settlement_difference": 0.0,
        "brokerage": 0.0,
        "profit": 0.0,
        "loss": 0.0,
        "net_pl": 0.0,
    }
    assert _sell_side_direction(amounts["net_pl"]) == "Settled"


def _subject2_allotment_row(status: str, shares: int, sold_price: float = 39.3) -> dict:
    return {
        "id": "a1",
        "position_id": "p1",
        "applicant_id": "x1",
        "status": status,
        "shares_allotted": shares,
        "cost_per_app": 84.5,
        "sold_price": sold_price,
        "is_sold": True,
        "sub_category": "10+",
        "ipo_applicants": {
            "name": "BHAVNA",
            "pan": "P",
            "dpid": "D",
            "party_id": "party1",
            "ipo_parties": {"id": "party1", "name": "SP-GIRISHBHAI"},
        },
    }


def _subject2_positions(stale_sell_rate: float | None = None) -> dict:
    pos = {"category": "Subject 2", "buy_rate": 84.5, "party": "SP-GIRISHBHAI"}
    if stale_sell_rate is not None:
        # Legacy key from the old sell-rate flow — settlement must ignore it.
        pos["s2_market_rate"] = stale_sell_rate
    return {"p1": pos}


def test_subject2_buy_line_uses_allotment_sold_price_never_sell_rate():
    # Definitive example: 364 shares, buy 84.5, allotment sold price 39.3.
    # Guaranteed 364 × 84.5 = 30,758; market 364 × 39.3 = 14,305.2.
    # Difference 16,452.8 → we owe the client (Payable) and collect from MAMA.
    # The sell trade's 90 sell_rate must NEVER be used for settlement.
    line = _build_line_from_allotment(
        _subject2_allotment_row("Allotted", 364), {}, _subject2_positions(stale_sell_rate=90)
    )
    assert line["line_type"] == "Subject 2"
    assert line["vyaj"] == pytest.approx(30758.0)  # guaranteed = 364 × 84.5
    assert line["sell_amt"] == pytest.approx(14305.2)  # market = 364 × 39.3
    assert line["net_pl"] == pytest.approx(-16452.8)  # only the difference settles
    assert line["direction"] == "Payable"


def test_subject2_buy_line_client_returns_excess_when_market_is_higher():
    # Guaranteed 30,758; market sale 364 × 90 = 32,760 already with the client.
    line = _build_line_from_allotment(
        _subject2_allotment_row("Allotted", 364, sold_price=90),
        {},
        _subject2_positions(),
    )
    assert line["vyaj"] == pytest.approx(30758.0)
    assert line["sell_amt"] == pytest.approx(32760.0)
    assert line["net_pl"] == pytest.approx(2002.0)  # only the difference settles
    assert line["direction"] == "Receivable"


def test_subject2_buy_line_client_gets_only_the_shortfall_when_market_is_lower():
    # Guaranteed 30,758; market sale 364 × 34.5 = 12,558 → pay 18,200, NOT 30,758.
    line = _build_line_from_allotment(
        _subject2_allotment_row("Allotted", 364, sold_price=34.5),
        {},
        _subject2_positions(),
    )
    assert line["vyaj"] == pytest.approx(30758.0)
    assert line["sell_amt"] == pytest.approx(12558.0)
    assert line["net_pl"] == pytest.approx(-18200.0)
    assert line["direction"] == "Payable"


def test_subject2_buy_line_zero_when_not_allotted():
    line = _build_line_from_allotment(
        _subject2_allotment_row("Not Allotted", 0), {}, _subject2_positions()
    )
    assert line["vyaj"] == 0.0
    assert line["sell_amt"] == 0.0
    assert line["net_pl"] == 0.0
    assert line["direction"] == "Settled"


def test_subject2_buy_line_pending_when_no_sold_price_recorded():
    # Even a recorded sell-trade rate must not substitute for the sold price.
    row = _subject2_allotment_row("Allotted", 364)
    row["is_sold"] = False
    line = _build_line_from_allotment(row, {}, _subject2_positions(stale_sell_rate=90))
    assert line["s2_pending"] is True
    assert line["net_pl"] == 0.0
    assert line["direction"] == "Settled"


def test_subject2_allotment_totals_use_sold_price():
    # Guaranteed / market legs come from attributed allotment rows; the contract
    # (shares × sell trade rate) is layered on top for the sell-party Vyaj.
    rows = [
        _subject2_allotment_row("Allotted", 364),  # sold at 39.3
        _subject2_allotment_row("Not Allotted", 0),
    ]
    totals = _subject2_allotment_totals(rows, buy_rate=84.5)
    assert totals["allotted"] == 1
    assert totals["shares_allotted"] == 364
    assert totals["guaranteed"] == pytest.approx(30758.0)
    assert totals["market"] == pytest.approx(14305.2)
    contract = totals["shares_allotted"] * 90  # sell trade rate 90
    amounts = _sell_side_financials(
        listing_sell_amt=totals["market"],
        seller_vyaj=contract,
        buyer_vyaj=totals["guaranteed"],
        recorded_brokerage=None,
        premium=False,
        subject2=True,
        client_amount=totals["guaranteed"],
    )
    # Client leg: Guaranteed − Market = 16,452.8 (Payable to client, buy side).
    assert amounts["settlement_difference"] == pytest.approx(16452.8)
    # MAMA leg: contract 32,760 Vyaj; Net = Market − Contract → 18,454.8 Receivable.
    assert amounts["vyaj"] == pytest.approx(32760.0)
    assert amounts["net_pl"] == pytest.approx(-18454.8)
    assert amounts["brokerage"] == pytest.approx(2002.0)  # 364 × (90 − 84.5)
    assert _sell_side_direction(amounts["net_pl"]) == "Receivable"


def test_subject2_allotment_totals_skip_unsold_rows():
    unsold = _subject2_allotment_row("Allotted", 364)
    unsold["is_sold"] = False
    totals = _subject2_allotment_totals([unsold], buy_rate=84.5)
    assert totals["shares_allotted"] == 364
    assert totals["guaranteed"] == 0.0
    assert totals["market"] == 0.0


def test_non_subject2_buy_line_unchanged():
    row = _subject2_allotment_row("Allotted", 364)
    line = _build_line_from_allotment(row, {}, {"p1": {"category": "IPO Application", "buy_rate": 84.5}})
    assert line["line_type"] == "Application"
    assert line["vyaj"] == pytest.approx(84.5)
    assert line["sell_amt"] == pytest.approx(364 * 39.3)


def test_effective_price_common_over_none_override():
    assert _effective_price({"listing_price_override": None}, 92.0) == 92.0
    assert _effective_price({"listing_price_override": ""}, 92.0) == 92.0
    assert _effective_price({"listing_price_override": 91}, 92.0) == 91.0
