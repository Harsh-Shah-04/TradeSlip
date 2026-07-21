"""Unit tests for the open-item ledger math (no live Supabase)."""

from __future__ import annotations

import pytest

from utils.ipo import ledger
from utils.ipo.ledger import (
    ACCOUNT_PARTY,
    ACCOUNT_SELL_PARTY,
    _account_column,
    _allocated_by_entry,
    _outstanding,
    balance_direction,
    charge_direction,
    charge_status,
    plan_allocations,
    side_to_account_type,
    summarize_entries,
)


def _charge(entry_id: str, amount: float, entry_type: str = "Settlement") -> dict:
    return {"id": entry_id, "amount": amount, "entry_type": entry_type}


def _payment(entry_id: str, amount: float, entry_type: str = "PaymentReceived") -> dict:
    return {"id": entry_id, "amount": amount, "entry_type": entry_type}


# ------------------------------------------------------------------- accounts


def test_account_column_maps_both_sides():
    assert _account_column(ACCOUNT_PARTY) == "party_id"
    assert _account_column(ACCOUNT_SELL_PARTY) == "sell_party_id"
    with pytest.raises(ValueError):
        _account_column("nonsense")


def test_side_to_account_type():
    assert side_to_account_type("seller") == ACCOUNT_SELL_PARTY
    assert side_to_account_type("sellers") == ACCOUNT_SELL_PARTY
    assert side_to_account_type("buyer") == ACCOUNT_PARTY
    assert side_to_account_type(None) == ACCOUNT_PARTY


# -------------------------------------------------------------------- statuses


def test_charge_status_lifecycle():
    assert charge_status(5000, 0) == "Pending"
    assert charge_status(5000, 2000) == "Part paid"
    assert charge_status(5000, 5000) == "Done"
    # Sub-paisa remainders must not keep a row open forever.
    assert charge_status(5000, 4999.999) == "Done"


def test_charge_status_ignores_sign():
    assert charge_status(-16452.8, 0) == "Pending"
    assert charge_status(-16452.8, 16452.8) == "Done"


def test_charge_and_balance_direction():
    assert charge_direction(824) == "To receive"
    assert charge_direction(-824) == "To pay"
    assert charge_direction(0) == "Settled"
    assert balance_direction(824) == "Receivable"
    assert balance_direction(-824) == "Payable"
    assert balance_direction(0) == "Settled"


def test_outstanding_never_goes_negative():
    assert _outstanding(1000, 1200) == 0.0
    assert _outstanding(-1000, 400) == 600.0


# ------------------------------------------------------------------ allocations


def test_allocated_by_entry_totals_both_sides():
    allocations = [
        {"charge_id": "c1", "payment_id": "p1", "amount": 100},
        {"charge_id": "c2", "payment_id": "p1", "amount": 250},
        {"charge_id": "c1", "payment_id": "p2", "amount": 50},
    ]
    totals = _allocated_by_entry(allocations)
    assert totals["c1"] == pytest.approx(150)
    assert totals["c2"] == pytest.approx(250)
    assert totals["p1"] == pytest.approx(350)  # payment applied in full
    assert totals["p2"] == pytest.approx(50)


def test_plan_allocations_full_payment_clears_everything():
    charges = [
        {"id": "c1", "outstanding": 5000},
        {"id": "c2", "outstanding": 3000},
    ]
    plan = plan_allocations(charges, 8000)
    assert plan == [
        {"charge_id": "c1", "amount": 5000.0},
        {"charge_id": "c2", "amount": 3000.0},
    ]


def test_plan_allocations_partial_payment_is_oldest_first():
    charges = [
        {"id": "c1", "outstanding": 5000},
        {"id": "c2", "outstanding": 3000},
    ]
    plan = plan_allocations(charges, 6000)
    assert plan == [
        {"charge_id": "c1", "amount": 5000.0},
        {"charge_id": "c2", "amount": 1000.0},
    ]


def test_plan_allocations_leaves_remainder_on_account():
    charges = [{"id": "c1", "outstanding": 1000}]
    plan = plan_allocations(charges, 2500)
    # Only the open 1,000 is consumed; the other 1,500 stays unallocated.
    assert plan == [{"charge_id": "c1", "amount": 1000.0}]


def test_plan_allocations_skips_already_settled_charges():
    charges = [
        {"id": "c1", "outstanding": 0},
        {"id": "c2", "outstanding": 400},
    ]
    assert plan_allocations(charges, 400) == [{"charge_id": "c2", "amount": 400.0}]


# --------------------------------------------------------------------- summary


def test_summarize_entries_splits_receive_and_pay():
    entries = [
        _charge("c1", 5000),      # SBI — they owe us
        _charge("c2", -16452.8),  # Subject 2 — we owe them
    ]
    summary = summarize_entries(entries, {})
    assert summary["to_receive"] == pytest.approx(5000)
    assert summary["to_pay"] == pytest.approx(16452.8)
    assert summary["open_count"] == 2
    assert summary["balance"] == pytest.approx(-11452.8)


def test_summarize_entries_closes_paid_charges():
    entries = [
        _charge("c1", 5000),
        _payment("p1", -5000),
    ]
    summary = summarize_entries(entries, {"c1": 5000.0, "p1": 5000.0})
    assert summary["open_count"] == 0
    assert summary["to_receive"] == 0.0
    assert summary["on_account"] == 0.0
    assert summary["balance"] == pytest.approx(0)


def test_summarize_entries_counts_partial_payment():
    entries = [
        _charge("c1", 5000),
        _payment("p1", -2000),
    ]
    summary = summarize_entries(entries, {"c1": 2000.0, "p1": 2000.0})
    assert summary["open_count"] == 1
    assert summary["to_receive"] == pytest.approx(3000)
    assert summary["balance"] == pytest.approx(3000)


def test_summarize_entries_reports_unapplied_payment_as_on_account():
    # Karan pays 8,000 against a 5,000 charge — 3,000 sits on account.
    entries = [
        _charge("c1", 5000),
        _payment("p1", -8000),
    ]
    summary = summarize_entries(entries, {"c1": 5000.0, "p1": 5000.0})
    assert summary["open_count"] == 0
    assert summary["on_account"] == pytest.approx(3000)
    assert summary["balance"] == pytest.approx(-3000)


def test_summarize_entries_empty_account_is_all_zero():
    summary = summarize_entries([], {})
    assert summary == {
        "balance": 0.0,
        "to_receive": 0.0,
        "to_pay": 0.0,
        "on_account": 0.0,
        "open_count": 0,
    }


# ----------------------------------------------------------- editing guards


def test_add_adjustment_rejects_zero_amount():
    with pytest.raises(ValueError, match="not be zero"):
        ledger.add_adjustment(
            account_type=ACCOUNT_PARTY,
            account_id="p1",
            amount=0,
            entry_date="2026-07-21",
            notes="carry forward",
        )


def test_add_adjustment_requires_a_reason():
    # A manual entry with no explanation is unauditable five months later.
    with pytest.raises(ValueError, match="short reason"):
        ledger.add_adjustment(
            account_type=ACCOUNT_PARTY,
            account_id="p1",
            amount=5000,
            entry_date="2026-07-21",
            notes="   ",
        )


def test_settlement_entries_cannot_be_edited(monkeypatch):
    """Editing a settlement charge here would desync it from Settlement & Reports."""
    monkeypatch.setattr(
        ledger,
        "_get_entry",
        lambda entry_id: {"id": entry_id, "entry_type": "Settlement", "party_id": "p1"},
    )
    with pytest.raises(ValueError, match="cannot be edited"):
        ledger.update_ledger_entry("e1", amount=999)


def test_settlement_entries_cannot_be_deleted(monkeypatch):
    monkeypatch.setattr(
        ledger,
        "_get_entry",
        lambda entry_id: {"id": entry_id, "entry_type": "Settlement", "party_id": "p1"},
    )
    with pytest.raises(ValueError, match="cannot be deleted"):
        ledger.delete_ledger_entry("e1")


def test_adjustment_cannot_shrink_below_what_was_already_paid(monkeypatch):
    monkeypatch.setattr(
        ledger,
        "_get_entry",
        lambda entry_id: {"id": entry_id, "entry_type": "Adjustment", "party_id": "p1"},
    )
    monkeypatch.setattr(
        ledger,
        "_fetch_allocations",
        lambda charge_ids=None: [{"charge_id": "e1", "payment_id": "pay1", "amount": 800}],
    )
    with pytest.raises(ValueError, match="already"):
        ledger.update_ledger_entry("e1", amount=500)


def test_entry_account_reads_either_side():
    assert ledger._entry_account({"party_id": "p1"}) == (ACCOUNT_PARTY, "p1")
    assert ledger._entry_account({"sell_party_id": "s1", "party_id": None}) == (
        ACCOUNT_SELL_PARTY,
        "s1",
    )


# ------------------------------------------------------------ funding trace


def _sell_line(sell_party, buy_party, net_pl, brokerage=0.0, line_type="Application"):
    return {
        "sell_party": sell_party,
        "buy_party": buy_party,
        "line_type": line_type,
        "net_pl": net_pl,
        "brokerage": brokerage,
        "applied": 1,
        "allotted": 1,
        "shares_allotted": 100,
        "market_amount": 0.0,
        "vyaj": 0.0,
    }


def test_charge_breakdown_traces_which_sell_parties_fund_a_client(monkeypatch):
    """KARAN-SELF is paid out of AIRAN's and MAMA's money — show which is which."""
    from utils.ipo import settlement

    monkeypatch.setattr(
        settlement,
        "_grey_market_sell_side",
        lambda ipo_id: {
            "sell_lines": [
                _sell_line("AIRAN", "KARAN-SELF", -35194.8, 900.0),
                _sell_line("MAMA", "KARAN-SELF", -8229.5, 1000.0),
                _sell_line("MAMA", "PINAL", -39200.0, 0.0),  # other client → excluded
            ]
        },
    )
    monkeypatch.setattr(settlement, "_finalized_settlement_id", lambda ipo_id: None)

    out = settlement.charge_breakdown(
        account_type="party", account_id="p1", account_name="KARAN-SELF", ipo_id="i1"
    )
    assert [f["counterparty"] for f in out["funding"]] == ["AIRAN", "MAMA"]
    assert out["funding"][0]["amount"] == pytest.approx(35194.8)
    assert out["funding"][1]["amount"] == pytest.approx(8229.5)
    # Money in, minus the 41,524.30 paid out, leaves exactly the brokerage.
    assert out["funding_total"] == pytest.approx(43424.3)
    assert out["funding_total"] - 41524.3 == pytest.approx(900.0 + 1000.0)


def test_charge_breakdown_for_a_sell_party_shows_where_its_money_goes(monkeypatch):
    from utils.ipo import settlement

    monkeypatch.setattr(
        settlement,
        "_grey_market_sell_side",
        lambda ipo_id: {
            "sell_lines": [
                _sell_line("MAMA", "KARAN-SELF", -8229.5),
                _sell_line("MAMA", "PINAL", -39200.0),
                _sell_line("AIRAN", "KARAN-SELF", -35194.8),  # other seller → excluded
            ]
        },
    )
    out = settlement.charge_breakdown(
        account_type="sell_party", account_id="s1", account_name="MAMA", ipo_id="i1"
    )
    assert [f["counterparty"] for f in out["funding"]] == ["PINAL", "KARAN-SELF"]
    assert out["funding_total"] == pytest.approx(47429.5)
    # Sell parties have no applicant accounts of their own.
    assert out["applicants"] == []


def test_charge_breakdown_keeps_line_types_apart(monkeypatch):
    """Premium and Application with the same pair must not be clubbed."""
    from utils.ipo import settlement

    monkeypatch.setattr(
        settlement,
        "_grey_market_sell_side",
        lambda ipo_id: {
            "sell_lines": [
                _sell_line("MAMA", "SACHIN", -1000.0, line_type="Application"),
                _sell_line("MAMA", "SACHIN", -2000.0, line_type="Premium"),
            ]
        },
    )
    monkeypatch.setattr(settlement, "_finalized_settlement_id", lambda ipo_id: None)
    out = settlement.charge_breakdown(
        account_type="party", account_id="p1", account_name="SACHIN", ipo_id="i1"
    )
    assert len(out["funding"]) == 2
    assert {f["line_type"] for f in out["funding"]} == {"Application", "Premium"}


def test_charge_breakdown_matches_on_name_case_insensitively(monkeypatch):
    from utils.ipo import settlement

    monkeypatch.setattr(
        settlement,
        "_grey_market_sell_side",
        lambda ipo_id: {"sell_lines": [_sell_line("MAMA", "karan-self", -500.0)]},
    )
    monkeypatch.setattr(settlement, "_finalized_settlement_id", lambda ipo_id: None)
    out = settlement.charge_breakdown(
        account_type="party", account_id="p1", account_name="KARAN-SELF", ipo_id="i1"
    )
    assert out["funding_total"] == pytest.approx(500.0)


# ------------------------------------------------- sell-party ledger sign flip


def test_sell_party_ledger_nets_flip_the_sell_side_sign(monkeypatch):
    """Sell-side net_pl is inverted; the ledger must always be '+ they owe us'.

    Ambica: net_pl −3,990 → _sell_side_direction says Receivable → ledger +3,990.
    Mama:   net_pl +1,820 → Payable → ledger −1,820.
    """
    from utils.ipo import settlement

    monkeypatch.setattr(
        settlement,
        "_grey_market_sell_side",
        lambda ipo_id: {
            "sell_lines": [
                {"sell_party": "AMBICA", "net_pl": -3990.0},
                {"sell_party": "MAMA", "net_pl": 1820.0},
                {"sell_party": "—", "net_pl": 999.0},  # unnamed → skipped
            ]
        },
    )
    monkeypatch.setattr(
        settlement,
        "sell_party_ids_by_name",
        lambda names: {"ambica": "sp-1", "mama": "sp-2"},
    )

    nets = settlement._sell_party_ledger_nets("ipo-1")
    assert nets == {"sp-1": 3990.0, "sp-2": -1820.0}


def test_sell_party_ledger_nets_sums_multiple_trades_per_party(monkeypatch):
    from utils.ipo import settlement

    monkeypatch.setattr(
        settlement,
        "_grey_market_sell_side",
        lambda ipo_id: {
            "sell_lines": [
                {"sell_party": "AMBICA", "net_pl": -1000.0},
                {"sell_party": "ambica", "net_pl": -500.0},  # same party, other case
            ]
        },
    )
    monkeypatch.setattr(
        settlement, "sell_party_ids_by_name", lambda names: {"ambica": "sp-1"}
    )
    assert settlement._sell_party_ledger_nets("ipo-1") == {"sp-1": 1500.0}


def test_sell_party_ledger_nets_skips_parties_missing_from_master(monkeypatch):
    from utils.ipo import settlement

    monkeypatch.setattr(
        settlement,
        "_grey_market_sell_side",
        lambda ipo_id: {"sell_lines": [{"sell_party": "GHOST", "net_pl": -100.0}]},
    )
    monkeypatch.setattr(settlement, "sell_party_ids_by_name", lambda names: {})
    assert settlement._sell_party_ledger_nets("ipo-1") == {}


def test_balance_equals_open_items_minus_on_account():
    """The headline balance must always reconcile to the open-item breakdown."""
    entries = [
        _charge("c1", 5000),
        _charge("c2", -2000),
        _payment("p1", -1500),
        _payment("p2", 500, "PaymentPaid"),
    ]
    allocated = {"c1": 1500.0, "p1": 1500.0, "c2": 500.0, "p2": 500.0}
    s = summarize_entries(entries, allocated)
    assert s["to_receive"] == pytest.approx(3500)
    assert s["to_pay"] == pytest.approx(1500)
    assert s["on_account"] == pytest.approx(0)
    assert s["balance"] == pytest.approx(s["to_receive"] - s["to_pay"])
