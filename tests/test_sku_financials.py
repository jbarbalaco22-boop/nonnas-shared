"""Tests for sku_financials.py, using the real line data from a live test JournalEntry
(ID 3887, DocNumber A2XSH-10Aug-12Aug-281, 2026-08-18) rather than invented fixtures - the
whole point of this module is parsing a real, unstructured text format correctly."""
from nonnas_shared.connectors.sku_financials import (
    _parse_line_description,
    compute_sku_revenue,
    sole_active_sku_as_of,
)

_SKU_REGISTRY = {
    "OO-OO-ORG-500": {"name": "Nonna's Olive Oil (500mL Original)", "status": "active"},
    "OO-OO-COOK-750ML-SHIP": {"name": "Nonna's Olive Oil 750mL Cooking & Sautéing", "status": "upcoming"},
}

# The exact Line array from JournalEntry 3887 - see this file's own header for the source.
_REAL_JE_3887 = {
    "Id": "3887",
    "DocNumber": "A2XSH-10Aug-12Aug-281",
    "Line": [
        {
            "Description": "DiscountNotTaxed  - OO-OO-ORG-500 - subscription_contract_checkout_one",
            "Amount": 3.24,
            "JournalEntryLineDetail": {
                "PostingType": "Debit",
                "AccountRef": {"name": "Discounts & Promotions"},
                "ClassRef": {"name": "DTC"},
            },
        },
        {
            "Description": "ProductSalesNotTaxed  - OO-OO-ORG-500 - Online store",
            "Amount": 27.0,
            "JournalEntryLineDetail": {
                "PostingType": "Credit",
                "AccountRef": {"name": "Product Revenue – DTC"},
                "ClassRef": {"name": "DTC"},
            },
        },
        {
            "Description": "ProductSalesNotTaxed  - OO-OO-ORG-500 - subscription_contract_checkout_one",
            "Amount": 49.14,
            "JournalEntryLineDetail": {
                "PostingType": "Credit",
                "AccountRef": {"name": "Product Revenue – DTC"},
                "ClassRef": {"name": "DTC"},
            },
        },
        {
            "Description": "ShippingNotTaxed  - Online store",
            "Amount": 5.0,
            "JournalEntryLineDetail": {
                "PostingType": "Credit",
                "AccountRef": {"name": "Shipping Revenue"},
                "ClassRef": {"name": "DTC"},
            },
        },
        {
            "Description": "ShippingNotTaxed  - subscription_contract_checkout_one",
            "Amount": 10.0,
            "JournalEntryLineDetail": {
                "PostingType": "Credit",
                "AccountRef": {"name": "Shipping Revenue"},
                "ClassRef": {"name": "DTC"},
            },
        },
        {
            "Description": "ShopifyFee  - Online store",
            "Amount": 1.16,
            "JournalEntryLineDetail": {
                "PostingType": "Debit",
                "AccountRef": {"name": "Shopify Transaction Fees"},
                "ClassRef": {"name": "DTC"},
            },
        },
        {
            "Description": "ShopifyFee  - subscription_contract_checkout_one",
            "Amount": 2.11,
            "JournalEntryLineDetail": {
                "PostingType": "Debit",
                "AccountRef": {"name": "Shopify Transaction Fees"},
                "ClassRef": {"name": "DTC"},
            },
        },
        {
            "Description": "Balance of settlement for: 2026-08-10",
            "Amount": 84.63,
            "JournalEntryLineDetail": {
                "PostingType": "Debit",
                "AccountRef": {"name": "Cash & Bank:Highbeam Checking Account - 9625"},
                "ClassRef": {"name": "DTC"},
            },
        },
    ],
}


def _line(description, amount, posting_type, account, class_name="DTC"):
    return {
        "Description": description,
        "Amount": amount,
        "JournalEntryLineDetail": {
            "PostingType": posting_type,
            "AccountRef": {"name": account},
            "ClassRef": {"name": class_name},
        },
    }


def test_parses_product_sales_line_with_known_sku():
    result = _parse_line_description(
        "ProductSalesNotTaxed  - OO-OO-ORG-500 - Online store", {"OO-OO-ORG-500"}
    )
    assert result == {
        "type": "ProductSalesNotTaxed", "sku": "OO-OO-ORG-500", "suffix": "Online store",
        "no_sku_segment": False,
    }


def test_parses_discount_line_with_known_sku():
    result = _parse_line_description(
        "DiscountNotTaxed  - OO-OO-ORG-500 - subscription_contract_checkout_one", {"OO-OO-ORG-500"}
    )
    assert result["sku"] == "OO-OO-ORG-500"
    assert result["type"] == "DiscountNotTaxed"


def test_shipping_line_has_no_sku():
    """Shipping isn't product-specific - "Online store" must not be misread as a SKU."""
    result = _parse_line_description("ShippingNotTaxed  - Online store", {"OO-OO-ORG-500"})
    assert result["sku"] is None
    assert result["type"] == "ShippingNotTaxed"


def test_unregistered_sku_not_recognized():
    """A SKU-shaped string that isn't in the registry is NOT treated as a SKU - avoids
    misreading a brand-new, not-yet-registered SKU as ordinary suffix text, or vice versa."""
    result = _parse_line_description(
        "ProductSalesNotTaxed  - OO-OO-NEW-999 - Online store", {"OO-OO-ORG-500"}
    )
    assert result["sku"] is None
    assert result["no_sku_segment"] is False  # a segment IS present, just unrecognized - never
    # defaulted to the sole active SKU, unlike a genuinely segment-less old-format line.


def test_pre_sku_posting_line_has_no_segment_at_all():
    """Old-format description, from before A2X started embedding SKU in the text at all."""
    result = _parse_line_description("ProductSalesNotTaxed  - Online store", {"OO-OO-ORG-500"})
    assert result["sku"] is None
    assert result["no_sku_segment"] is True


def test_settlement_balance_line_has_no_type_match_issue():
    result = _parse_line_description("Balance of settlement for: 2026-08-10", {"OO-OO-ORG-500"})
    assert result["sku"] is None


def test_compute_sku_revenue_matches_real_journal_entry():
    """End-to-end against the real JE 3887 line data - revenue, discount, and net must match
    what was independently verified against the source CSV export (27.00 + 22.14 = 49.14 for
    the two "subscription" orders, plus 27.00 for the separate "Online store" order)."""
    result = compute_sku_revenue([_REAL_JE_3887], _SKU_REGISTRY)

    assert set(result.keys()) == {"OO-OO-ORG-500"}
    sku = result["OO-OO-ORG-500"]
    assert sku["name"] == "Nonna's Olive Oil (500mL Original)"
    assert sku["revenue"] == 27.0 + 49.14
    assert sku["discounts"] == -3.24  # Debit to a contra-revenue account -> negative
    assert sku["refunds"] == 0.0
    assert sku["net"] == 27.0 + 49.14 - 3.24


def test_compute_sku_revenue_ignores_shipping_fees_and_settlement():
    """Only the Product Revenue/Discounts & Promotions accounts count - the other five lines in
    JE 3887 (shipping x2, fees x2, settlement balance) must not leak into the SKU total."""
    result = compute_sku_revenue([_REAL_JE_3887], _SKU_REGISTRY)
    # 27.00 + 49.14 - 3.24 = 72.90, NOT inflated by shipping (15.00) or reduced by fees (3.27)
    assert result["OO-OO-ORG-500"]["net"] == 72.90


def test_compute_sku_revenue_includes_taxed_product_sales_type():
    """A2X posts a second, undocumented type for taxed orders - "ProductSales" (no "NotTaxed"
    suffix) - to the exact same Product Revenue account. This is real revenue and was silently
    dropped by an earlier version of this module that matched on the type string instead of the
    destination account; a real July 2026 pull turned up $177.14 of it. Confirmed real via the
    live QBO account (Product Revenue - DTC) rather than assumed from the string's shape."""
    je = {"DocNumber": "A2XSH-14Jul-16Jul-076", "Line": [
        _line("ProductSales  - subscription_contract_checkout_one", 88.56, "Credit", "Product Revenue – DTC"),
    ]}
    result = compute_sku_revenue([je], _SKU_REGISTRY)
    assert result["OO-OO-ORG-500"]["revenue"] == 88.56


def test_compute_sku_revenue_includes_refunds_and_chargebacks():
    """Returns & Refunds and Chargebacks are real revenue-reducing events tied to a specific
    SKU's units, not tracked at all by the original revenue/discounts-only version of this
    module - a real July 2026 pull had $103.85 of exactly this, matching QBO's own Returns &
    Refunds account total for DTC that period."""
    je = {"DocNumber": "A2XSH-01Jul-06Jul-984", "Line": [
        _line("RefundNotTaxed  - Online store", 35.0, "Debit", "Returns & Refunds"),
        _line("RefundNotTaxed  - subscription_contract_checkout_one", 68.85, "Debit", "Returns & Refunds"),
        _line("Chargeback  - Online store", 12.0, "Debit", "Chargebacks"),
    ]}
    result = compute_sku_revenue([je], _SKU_REGISTRY)
    assert result["OO-OO-ORG-500"]["refunds"] == -(35.0 + 68.85 + 12.0)
    assert result["OO-OO-ORG-500"]["net"] == -(35.0 + 68.85 + 12.0)


def test_compute_sku_revenue_excludes_amazon_tagged_lines_on_shared_accounts():
    """Discounts & Promotions / Returns & Refunds are shared account NAMES across channels -
    native Amazon (A2XUS-) entries post to the same names, just tagged Class=Amazon. Without a
    Class=DTC check on those two accounts specifically, Amazon's own discounts/refunds would
    silently blend into what's labeled a DTC-only tool. Product Revenue doesn't need this check
    since the account name itself already encodes the channel ("Product Revenue - DTC" vs
    "- Amazon"), but this test uses the shared-name accounts where contamination is possible."""
    je = {"DocNumber": "A2XUS-07Jul-21Jul-693", "Line": [
        _line("Discount  - Some Amazon Coupon", 50.0, "Debit", "Discounts & Promotions", class_name="Amazon"),
        _line("Refund  - Some Amazon Return", 30.0, "Debit", "Returns & Refunds", class_name="Amazon"),
    ]}
    assert compute_sku_revenue([je], _SKU_REGISTRY) == {}


def test_compute_sku_revenue_sums_across_multiple_journal_entries():
    je_a = {"DocNumber": "A2XSH-01Aug-03Aug-100", "Line": [
        _line("ProductSalesNotTaxed  - OO-OO-ORG-500 - Online store", 10.0, "Credit", "Product Revenue – DTC"),
    ]}
    je_b = {"DocNumber": "A2XUS-01Aug-03Aug-200", "Line": [
        _line("ProductSalesNotTaxed  - OO-OO-ORG-500 - Online store", 5.0, "Credit", "Product Revenue – DTC"),
    ]}
    result = compute_sku_revenue([je_a, je_b], _SKU_REGISTRY)
    assert result["OO-OO-ORG-500"]["revenue"] == 15.0


def test_compute_sku_revenue_empty_input_gives_empty_dict():
    assert compute_sku_revenue([], _SKU_REGISTRY) == {}


def test_compute_sku_revenue_defaults_pre_sku_posting_lines_to_sole_active_sku():
    """Old-format July JE, from before A2X started embedding SKU in the Description text at
    all - two segments, not three. OO-OO-ORG-500 is the only real SKU that could have sold in
    that period (only one active SKU in the registry), so this must default to it rather than
    getting silently dropped."""
    old_format_je = {"DocNumber": "A2XSH-01Jul-03Jul-050", "Line": [
        _line("ProductSalesNotTaxed  - Online store", 100.0, "Credit", "Product Revenue – DTC"),
        _line("DiscountNotTaxed  - Online store", 10.0, "Debit", "Discounts & Promotions"),
        _line("ShippingNotTaxed  - Online store", 5.0, "Credit", "Shipping Revenue"),
    ]}
    result = compute_sku_revenue([old_format_je], _SKU_REGISTRY)
    assert set(result.keys()) == {"OO-OO-ORG-500"}
    assert result["OO-OO-ORG-500"]["revenue"] == 100.0
    assert result["OO-OO-ORG-500"]["discounts"] == -10.0
    assert result["OO-OO-ORG-500"]["net"] == 90.0  # shipping still excluded either way


def test_compute_sku_revenue_no_default_when_multiple_active_skus():
    """Once a second SKU goes active, a segment-less line is genuinely ambiguous - don't guess
    which one it belongs to. The fallback must turn itself off automatically here."""
    two_active_registry = {
        "OO-OO-ORG-500": {"name": "Nonna's Olive Oil (500mL Original)", "status": "active"},
        "OO-OO-COOK-750ML-SHIP": {"name": "Nonna's Olive Oil 750mL Cooking & Sautéing", "status": "active"},
    }
    old_format_je = {"DocNumber": "A2XSH-01Sep-03Sep-060", "Line": [
        _line("ProductSalesNotTaxed  - Online store", 100.0, "Credit", "Product Revenue – DTC"),
    ]}
    assert compute_sku_revenue([old_format_je], two_active_registry) == {}


def test_compute_sku_revenue_does_not_default_an_unrecognized_sku_segment():
    """A line WITH a SKU-shaped segment that just isn't registered must stay excluded, not get
    swept into the sole active SKU's total - it might genuinely be a different product."""
    je = {"DocNumber": "A2XSH-01Jul-03Jul-070", "Line": [
        _line("ProductSalesNotTaxed  - OO-OO-NEW-999 - Online store", 100.0, "Credit", "Product Revenue – DTC"),
    ]}
    assert compute_sku_revenue([je], _SKU_REGISTRY) == {}


def test_compute_sku_revenue_ignores_non_a2x_journal_entries():
    """A TikTok/LinkMyBooks (or any non-A2X) entry must never contribute here, even if its
    Description text happens to match the "TYPE - SKU - suffix" shape by coincidence and even if
    it were somehow tagged onto the right account/class - only A2XSH-/A2XUS- DocNumber prefixes
    are trusted as this parser's actual source format."""
    non_a2x_je = {"DocNumber": "LMB-TT-01Aug-200", "Line": [
        _line("ProductSalesNotTaxed  - OO-OO-ORG-500 - TikTok Shop", 999.0, "Credit", "Product Revenue – DTC"),
    ]}
    assert compute_sku_revenue([non_a2x_je], _SKU_REGISTRY) == {}


def test_compute_sku_revenue_ignores_journal_entry_with_no_docnumber():
    no_doc_je = {"Line": [
        _line("ProductSalesNotTaxed  - OO-OO-ORG-500 - Online store", 10.0, "Credit", "Product Revenue – DTC"),
    ]}
    assert compute_sku_revenue([no_doc_je], _SKU_REGISTRY) == {}


def test_compute_sku_revenue_ignores_lines_on_unrelated_accounts():
    """Fees, COGS, and other non-revenue/discount/refund accounts must not count even if they
    happen to carry an A2X-shaped Description and ClassRef=DTC."""
    je = {"DocNumber": "A2XSH-01Jul-03Jul-090", "Line": [
        _line("ShopifyFee  - OO-OO-ORG-500 - Online store", 5.0, "Debit", "Shopify Transaction Fees"),
    ]}
    assert compute_sku_revenue([je], _SKU_REGISTRY) == {}


# ---- Per-transaction-date fallback (a second SKU's launch must not retroactively make older,
# unambiguous transactions ambiguous) - the real scenario: OO-OO-ORG-501 (a 3-pack of the same
# product) went active 2026-08-14, confirmed against its first real order. ----

_TWO_SKU_REGISTRY = {
    "OO-OO-ORG-500": {"name": "Nonna's Olive Oil (500mL Original)", "status": "active"},
    "OO-OO-ORG-501": {"name": "Nonna's Olive Oil (500mL Original, 3-Pack)", "status": "active", "active_since": "2026-08-14"},
}


def test_sole_active_sku_as_of_before_second_sku_launches():
    assert sole_active_sku_as_of(_TWO_SKU_REGISTRY, "2026-08-13") == "OO-OO-ORG-500"


def test_sole_active_sku_as_of_on_and_after_second_sku_launches():
    assert sole_active_sku_as_of(_TWO_SKU_REGISTRY, "2026-08-14") is None
    assert sole_active_sku_as_of(_TWO_SKU_REGISTRY, "2026-09-01") is None


def test_sole_active_sku_as_of_missing_active_since_means_always_active():
    registry = {"OO-OO-ORG-500": {"name": "x", "status": "active"}}
    assert sole_active_sku_as_of(registry, "2020-01-01") == "OO-OO-ORG-500"


def test_compute_sku_revenue_still_defaults_for_old_transaction_after_second_sku_registered():
    """A July JE, dated well before OO-OO-ORG-501's 2026-08-14 launch, must still default to
    OO-OO-ORG-500 even though the registry now has two active SKUs - the July JE's own TxnDate
    makes it unambiguous, regardless of what's true today."""
    je = {
        "DocNumber": "A2XSH-01Jul-03Jul-050", "TxnDate": "2026-07-02",
        "Line": [_line("ProductSalesNotTaxed  - Online store", 100.0, "Credit", "Product Revenue – DTC")],
    }
    result = compute_sku_revenue([je], _TWO_SKU_REGISTRY)
    assert result["OO-OO-ORG-500"]["revenue"] == 100.0


def test_compute_sku_revenue_no_default_for_transaction_on_or_after_second_sku_launch():
    """A JE dated on/after OO-OO-ORG-501's launch is genuinely ambiguous - both SKUs were active
    by then, so a segment-less line must be excluded, not guessed."""
    je = {
        "DocNumber": "A2XSH-14Aug-16Aug-200", "TxnDate": "2026-08-15",
        "Line": [_line("ProductSalesNotTaxed  - Online store", 100.0, "Credit", "Product Revenue – DTC")],
    }
    assert compute_sku_revenue([je], _TWO_SKU_REGISTRY) == {}
