"""Tests for channel_financials.py — especially the negative-value handling
that caused a real bug: QBO reports discounts/refunds as negative numbers
within the Income section, so they must be ADDED (not subtracted again) to
correctly reduce gross revenue down to net sales."""
from nonnas_shared.connectors.channel_financials import (
    compute_channel_margin,
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


def _margin_account_map() -> dict:
    return {
        "net_sales": {
            "dtc": {"include": ["41100 Product Revenue - DTC"], "include_tagged_class": "DTC"},
        },
        "cogs": ["51100 Bulk Olive Oil - Raw"],
        "three_pl": ["52100 3PL Fulfillment Fees"],
        "ads": {"by_channel": {"DTC": ["61100 Meta Ads"]}, "unallocated": ["61150 Marketplace Advertising"]},
        "fees": {"dtc": ["55100 Shopify Transaction Fees"]},
    }


def test_channel_margin_computes_contribution():
    pl_data = {
        "Income": {"41100 Product Revenue - DTC": {"DTC": 1000.0}},
        "COGS": {"51100 Bulk Olive Oil - Raw": {"DTC": 300.0}},
        "Expenses": {
            "52100 3PL Fulfillment Fees": {"DTC": 50.0},
            "61100 Meta Ads": {"Not Specified": 100.0},
            "55100 Shopify Transaction Fees": {"Not Specified": 20.0},
        },
    }
    result = compute_channel_margin(pl_data, "DTC", _margin_account_map())
    assert result["net_sales"] == 1000.0
    assert result["cogs"] == 300.0
    assert result["three_pl"] == 50.0
    assert result["ads"] == 100.0  # not Class-tagged, allocated by account instead
    assert result["fees"] == 20.0
    assert result["contribution"] == 530.0  # 1000 - 300 - 50 - 100 - 20
    assert result["contribution_pct"] == 0.53


def test_channel_margin_unallocated_ads_excluded():
    pl_data = {
        "Income": {"41100 Product Revenue - DTC": {"DTC": 1000.0}},
        "Expenses": {"61150 Marketplace Advertising": {"Not Specified": 999.0}},
    }
    result = compute_channel_margin(pl_data, "DTC", _margin_account_map())
    assert result["ads"] == 0.0  # Marketplace Advertising is unallocated, not attributed to DTC


def test_channel_margin_zero_revenue_gives_none_not_zero_pct():
    result = compute_channel_margin({}, "DTC", _margin_account_map())
    assert result["net_sales"] == 0.0
    assert result["contribution_pct"] is None
