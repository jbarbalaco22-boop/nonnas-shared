"""Tests for channel_financials.py — especially the negative-value handling
that caused a real bug: QBO reports discounts/refunds as negative numbers
within the Income section, so they must be ADDED (not subtracted again) to
correctly reduce gross revenue down to net sales."""
from nonnas_shared.config import load_qbo_account_map
from nonnas_shared.connectors.channel_financials import (
    compute_channel_health_metrics,
    compute_channel_margin,
    compute_company_totals,
    compute_expense_totals,
    compute_net_income,
    compute_net_sales_by_channel,
    compute_overhead_by_account,
    compute_revenue_concentration,
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
    assert result["other_marketing"] == 0.0
    assert result["contribution"] == 530.0  # 1000 - 300 - 50 - 100 - 20
    assert result["contribution_pct"] == 0.53


def test_channel_margin_other_marketing_reduces_contribution_but_not_ads():
    """other_marketing (e.g. Email & SMS Marketing tooling) must reduce contribution the same
    as any other cost, but must NOT be folded into `ads` - that would distort ROAS
    (net_sales / ads), which is meant to measure working paid-media efficiency specifically."""
    account_map = {
        **_margin_account_map(),
        "other_marketing": {"by_channel": {"DTC": ["63100 Email & SMS Marketing"]}},
    }
    pl_data = {
        "Income": {"41100 Product Revenue - DTC": {"DTC": 1000.0}},
        "Expenses": {
            "61100 Meta Ads": {"Not Specified": 100.0},
            "63100 Email & SMS Marketing": {"DTC": 50.0},
        },
    }
    result = compute_channel_margin(pl_data, "DTC", account_map)
    assert result["ads"] == 100.0  # unchanged by other_marketing
    assert result["other_marketing"] == 50.0
    assert result["contribution"] == 850.0  # 1000 - 100 - 50


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


def test_channel_margin_discount_refund_rate_from_qbo_accounts():
    """Real audit finding, 2026-08-18: discount_rate/refund_rate used to come from Shopify order
    data, which can't be retrieved for months older than ~55 days - so these must come from the
    43100/43200 QBO accounts instead, which have the same full history as net_sales itself."""
    pl_data = {
        "Income": {
            "41100 Product Revenue - DTC": {"DTC": 1000.0},
            "43100 Discounts & Promotions": {"DTC": -150.0},
            "43200 Returns & Refunds": {"DTC": -25.0},
        },
    }
    result = compute_channel_margin(pl_data, "DTC", _margin_account_map())
    assert result["gross_revenue"] == 1000.0
    assert result["discount_amount"] == 150.0
    assert result["refund_amount"] == 25.0
    assert result["discount_rate"] == 0.15
    assert result["refund_rate"] == 0.025


def test_channel_margin_wholesale_discount_refund_rate_is_none():
    """Wholesale doesn't post to the shared 43100/43200 accounts (see
    _DISCOUNT_ACCOUNT_PREFIX's comment) - None, not a misleadingly precise 0.0%."""
    pl_data = {"Income": {"41110 Product Revenue - Wholesale": {"Wholesale": 500.0}}}
    result = compute_channel_margin(pl_data, "Wholesale", _margin_account_map())
    assert result["discount_rate"] is None
    assert result["refund_rate"] is None


def test_channel_margin_no_gross_gives_none_discount_rate_not_zero_division():
    result = compute_channel_margin({}, "DTC", _margin_account_map())
    assert result["discount_rate"] is None
    assert result["refund_rate"] is None


def test_channel_health_metrics_computes_all_four_ratios():
    # discount_rate/refund_rate are pass-through from channel_margin (QBO account-based) now,
    # not recomputed from shopify_totals - see compute_channel_margin's docstring for why.
    channel_margin = {"net_sales": 1000.0, "ads": 100.0, "discount_rate": 100.0 / 1100.0, "refund_rate": 50.0 / 1100.0}
    shopify_totals = {"orders": 20, "net_revenue": 950.0}
    result = compute_channel_health_metrics(channel_margin, shopify_totals)
    assert result["aov"] == 47.5  # 950 / 20
    assert result["discount_rate"] == 100.0 / 1100.0
    assert result["refund_rate"] == 50.0 / 1100.0
    assert result["roas"] == 10.0  # 1000 / 100


def test_channel_health_metrics_zero_denominators_give_none_not_zero():
    channel_margin = {"net_sales": 0.0, "ads": 0.0, "discount_rate": None, "refund_rate": None}
    shopify_totals = {"orders": 0, "net_revenue": 0.0}
    result = compute_channel_health_metrics(channel_margin, shopify_totals)
    assert result["aov"] is None
    assert result["discount_rate"] is None
    assert result["refund_rate"] is None
    assert result["roas"] is None


def test_revenue_concentration_sums_to_one():
    margins = {
        "DTC": {"net_sales": 500.0},
        "TikTok": {"net_sales": 300.0},
        "Amazon": {"net_sales": 150.0},
        "Wholesale": {"net_sales": 50.0},
    }
    result = compute_revenue_concentration(margins)
    assert result["DTC"] == 0.5
    assert result["TikTok"] == 0.3
    assert result["Amazon"] == 0.15
    assert result["Wholesale"] == 0.05
    assert abs(sum(result.values()) - 1.0) < 1e-9


def test_revenue_concentration_no_revenue_gives_all_zeros():
    margins = {"DTC": {"net_sales": 0.0}, "TikTok": {"net_sales": 0.0}}
    result = compute_revenue_concentration(margins)
    assert result == {"DTC": 0.0, "TikTok": 0.0}


def test_real_account_map_attributes_shared_shipping_revenue_to_every_channel():
    """Regression test: 42100 Shipping Revenue is one shared account with a column per
    Class, not a per-channel dedicated account like the 411x0 product revenue accounts.
    It was only in dtc's include list for a while, so TikTok/Amazon/Wholesale silently
    dropped their entire shipping revenue out of net sales - caught by comparing the
    dashboard against a real QBO P&L-by-Class export (Jan 1-Aug 17 2026), where TikTok
    was short by exactly its $5,280.00 shipping revenue line and Amazon by its $35.92
    line. Every channel with net sales rules must pull its own class column from this
    account, using the real packaged map (not a hand-built fixture) so a future edit to
    qbo_account_map.json that reintroduces the gap fails this test."""
    account_map = load_qbo_account_map()
    pl_data = {
        "Income": {
            "41100 Product Revenue – DTC": {"DTC": 100.0},
            "41110 Product Revenue – Wholesale": {"Wholesale": 100.0},
            "41120 Product Revenue – TikTok Shop": {"TikTok": 100.0},
            "41130 Product Revenue – Amazon": {"Amazon": 100.0},
            "42100 Shipping Revenue": {"DTC": 10.0, "TikTok": 20.0, "Amazon": 30.0, "Wholesale": 40.0},
        }
    }
    result = compute_net_sales_by_channel(pl_data, account_map)
    assert result["net_sales"]["dtc"] == 110.0
    assert result["net_sales"]["tiktok"] == 120.0
    assert result["net_sales"]["amazon"] == 130.0
    assert result["net_sales"]["wholesale"] == 140.0


def test_company_totals_sums_dollars_and_reweights_ratios():
    """AOV/discount_rate/refund_rate/roas must be recomputed from summed numerator/denominator,
    not averaged per-channel — this is what catches a naive `mean(channel_aovs)` regression.
    discount_rate/refund_rate now sum channel_margins' gross_revenue/discount_amount/
    refund_amount (QBO account-based), not shopify_totals' gross/discounts/refunds."""
    margins = {
        "DTC": {"net_sales": 1000.0, "cogs": 300.0, "three_pl": 50.0, "ads": 100.0, "fees": 20.0, "contribution": 530.0, "gross_revenue": 1100.0, "discount_amount": 100.0, "refund_amount": 50.0},
        "TikTok": {"net_sales": 500.0, "cogs": 150.0, "three_pl": 25.0, "ads": 50.0, "fees": 10.0, "contribution": 265.0, "gross_revenue": 550.0, "discount_amount": 30.0, "refund_amount": 0.0},
    }
    shopify_totals = {
        "DTC": {"orders": 20, "net_revenue": 950.0, "units": 40},
        "TikTok": {"orders": 10, "net_revenue": 520.0, "units": 20},
    }
    result = compute_company_totals(margins, shopify_totals)
    assert result["net_sales"] == 1500.0
    assert result["contribution"] == 795.0
    assert result["contribution_pct"] == 795.0 / 1500.0
    assert result["orders"] == 30
    assert result["units"] == 60
    assert result["aov"] == (950.0 + 520.0) / 30  # not mean(47.5, 52.0)
    assert result["discount_rate"] == 130.0 / 1650.0
    assert result["refund_rate"] == 50.0 / 1650.0
    assert result["roas"] == 1500.0 / 150.0


def test_company_totals_zero_denominators_give_none_not_zero():
    result = compute_company_totals(
        {"DTC": {"net_sales": 0.0, "ads": 0.0, "contribution": 0.0}},
        {"DTC": {"orders": 0, "gross": 0.0, "discounts": 0.0, "refunds": 0.0, "net_revenue": 0.0, "units": 0}},
    )
    assert result["contribution_pct"] is None
    assert result["aov"] is None
    assert result["discount_rate"] is None
    assert result["refund_rate"] is None
    assert result["roas"] is None


def test_net_income_computes_from_section_totals():
    pl_data = {
        "Income": {"41100 Product Revenue - DTC": {"DTC": 1000.0}},
        "COGS": {"51100 Bulk Olive Oil - Raw": {"DTC": 300.0}},
        "Expenses": {
            "61100 Meta Ads": {"Not Specified": 100.0},
            "71100 Founder Comp": {"Not Specified": 400.0},
        },
        "OtherIncome": {"90900 Interest Income": {"Not Specified": 10.0}},
    }
    result = compute_net_income(pl_data)
    assert result["income"] == 1000.0
    assert result["cogs"] == 300.0
    assert result["expenses"] == 500.0
    assert result["other_income"] == 10.0
    assert result["net_income"] == 210.0  # 1000 - 300 - 500 + 10


def test_net_income_missing_sections_default_to_zero():
    result = compute_net_income({"Income": {"41100 Product Revenue - DTC": {"DTC": 1000.0}}})
    assert result["cogs"] == 0.0
    assert result["expenses"] == 0.0
    assert result["other_income"] == 0.0
    assert result["net_income"] == 1000.0


def test_net_income_captures_costs_not_in_any_channel_bucket():
    """This is the whole point of computing net_income structurally rather than as
    contribution minus a hand-maintained overhead account list: a G&A account nobody added to
    qbo_account_map.json (or ad spend explicitly left unallocated, like Paid Collaborations)
    must still reduce net_income correctly, without needing its own account_map entry."""
    pl_data = {
        "Income": {"41100 Product Revenue - DTC": {"DTC": 1000.0}},
        "Expenses": {
            "61130 Paid Collaborations": {"Not Specified": 200.0},  # deliberately unallocated in ads
            "99999 Some Brand New Overhead Account": {"Not Specified": 50.0},  # not in any list at all
        },
    }
    result = compute_net_income(pl_data)
    assert result["net_income"] == 750.0  # 1000 - 200 - 50, both captured despite no account_map entry


def test_overhead_by_account_excludes_channel_allocated_accounts():
    pl_data = {
        "Expenses": {
            "51100 Bulk Olive Oil - Raw": {"Not Specified": 1000.0},  # cogs - excluded
            "61100 Meta Ads": {"Not Specified": 500.0},  # ads - excluded
            "55100 Shopify Transaction Fees": {"Not Specified": 50.0},  # fees - excluded
            "63100 Email & SMS Marketing": {"Not Specified": 75.0},  # other_marketing - excluded
            "71100 Founder/Officer Compensation": {"Not Specified": 5000.0},  # not claimed - kept
            "84100 Legal Fees": {"Not Specified": 200.0},  # not claimed - kept
        }
    }
    account_map = {
        "cogs": ["51100 Bulk Olive Oil - Raw"],
        "three_pl": [],
        "ads": {"accounts": ["61100 Meta Ads"]},
        "fees": {"dtc": ["55100 Shopify Transaction Fees"]},
        "other_marketing": {"by_channel": {"DTC": ["63100 Email & SMS Marketing"]}},
    }
    result = compute_overhead_by_account(pl_data, account_map)
    labels = {r["label"] for r in result}
    assert labels == {"71100 Founder/Officer Compensation", "84100 Legal Fees"}


def test_overhead_by_account_sorted_descending_and_skips_zero():
    pl_data = {
        "Expenses": {
            "84100 Legal Fees": {"Not Specified": 200.0},
            "71100 Founder/Officer Compensation": {"Not Specified": 5000.0},
            "86100 Bank Fees": {"Not Specified": 0.0},
        }
    }
    result = compute_overhead_by_account(pl_data, {})
    assert [r["label"] for r in result] == ["71100 Founder/Officer Compensation", "84100 Legal Fees"]
