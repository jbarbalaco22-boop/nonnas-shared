"""Tests for channel_financials.py — especially the negative-value handling
that caused a real bug: QBO reports discounts/refunds as negative numbers
within the Income section, so they must be ADDED (not subtracted again) to
correctly reduce gross revenue down to net sales."""
from nonnas_shared.connectors.channel_financials import (
    compute_expense_totals,
    compute_net_sales_by_channel,
)


def _account_map(net_sales: dict, **extra) -> dict:
    return {"net_sales": net_sales, **extra}


def test_net_sales_include_only():
    pl_data = {"Income": {"41100 Product Revenue - DTC": {"DTC": 1000.0}}}
    account_map = _account_map({
        "dtc": {"include": ["41100 Product Revenue - DTC"], "include_tagged_class": "DTC"},
    })
    result = compute_net_sales_by_channel(pl_data, account_map)
    assert result["net_sales"]["dtc"] == 1000.0
    assert result["warnings"] == []


def test_net_sales_subtracts_negative_discount_correctly():
    """Regression test for the sign bug: QBO discount/refund accounts already
    carry a negative value, so net sales = gross + deduction, not gross - deduction."""
    pl_data = {
        "Income": {
            "41100 Product Revenue - DTC": {"DTC": 1000.0},
            "43100 Discounts & Promotions": {"DTC": -150.0},
        }
    }
    account_map = _account_map({
        "dtc": {
            "include": ["41100 Product Revenue - DTC"],
            "include_tagged_class": "DTC",
            "subtract": ["43100"],
            "subtract_tagged_class": "DTC",
        },
    })
    result = compute_net_sales_by_channel(pl_data, account_map)
    assert result["net_sales"]["dtc"] == 850.0  # 1000 + (-150), not 1000 - (-150) = 1150


def test_prefix_matching():
    pl_data = {"Income": {"43100 Discounts & Promotions": {"DTC": -50.0}}}
    account_map = _account_map({
        "dtc": {"include": [], "subtract": ["43100"], "subtract_tagged_class": "DTC"},
    })
    result = compute_net_sales_by_channel(pl_data, account_map)
    assert result["net_sales"]["dtc"] == -50.0  # 0 include + (-50) subtract


def test_missing_subtract_list_warns_instead_of_guessing():
    pl_data = {"Income": {"41120 Product Revenue - TikTok": {"TikTok": 500.0}}}
    account_map = _account_map({
        "tiktok": {
            "include": ["41120 Product Revenue - TikTok"],
            "include_tagged_class": "TikTok",
            "subtract_tagged_class": "TikTok",  # no explicit "subtract" list
        },
    })
    result = compute_net_sales_by_channel(pl_data, account_map)
    assert result["net_sales"]["tiktok"] == 500.0  # no deduction applied
    assert len(result["warnings"]) == 1
    assert "tiktok" in result["warnings"][0]


def test_include_without_tagged_class_sums_all_columns():
    pl_data = {"Income": {"41110 Product Revenue - Wholesale": {"Wholesale": 200.0, "Not Specified": 28.0}}}
    account_map = _account_map({
        "wholesale": {"include": ["41110 Product Revenue - Wholesale"]},
    })
    result = compute_net_sales_by_channel(pl_data, account_map)
    assert result["net_sales"]["wholesale"] == 228.0


def test_expense_totals_sum_across_classes():
    pl_data = {
        "COGS": {"51100 Bulk Olive Oil - Raw": {"DTC": 300.0, "Amazon": 200.0}},
        "Expenses": {"52100 3PL Fulfillment Fees": {"Amazon": 150.0}},
    }
    account_map = {
        "cogs": ["51100 Bulk Olive Oil - Raw"],
        "three_pl": ["52100 3PL Fulfillment Fees"],
        "ads": {"accounts": []},
    }
    result = compute_expense_totals(pl_data, account_map)
    assert result["cogs"] == 500.0
    assert result["three_pl"] == 150.0
    assert result["ads"] == 0.0
