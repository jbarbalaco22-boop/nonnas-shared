"""Tests for sku_financials.py, using the real line data from a live test JournalEntry
(ID 3887, DocNumber A2XSH-10Aug-12Aug-281, 2026-08-18) rather than invented fixtures - the
whole point of this module is parsing a real, unstructured text format correctly."""
from nonnas_shared.connectors.sku_financials import _parse_line_description, compute_sku_revenue

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


def test_parses_product_sales_line_with_known_sku():
    result = _parse_line_description(
        "ProductSalesNotTaxed  - OO-OO-ORG-500 - Online store", {"OO-OO-ORG-500"}
    )
    assert result == {"type": "ProductSalesNotTaxed", "sku": "OO-OO-ORG-500", "suffix": "Online store"}


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
    assert sku["net"] == 27.0 + 49.14 - 3.24


def test_compute_sku_revenue_ignores_shipping_fees_and_settlement():
    """Only ProductSalesNotTaxed and DiscountNotTaxed count - the other five lines in JE 3887
    (shipping x2, fees x2, settlement balance) must not leak into the SKU total."""
    result = compute_sku_revenue([_REAL_JE_3887], _SKU_REGISTRY)
    # 27.00 + 49.14 - 3.24 = 72.90, NOT inflated by shipping (15.00) or reduced by fees (3.27)
    assert result["OO-OO-ORG-500"]["net"] == 72.90


def test_compute_sku_revenue_sums_across_multiple_journal_entries():
    je_a = {"DocNumber": "A2XSH-01Aug-03Aug-100", "Line": [{
        "Description": "ProductSalesNotTaxed  - OO-OO-ORG-500 - Online store",
        "Amount": 10.0,
        "JournalEntryLineDetail": {"PostingType": "Credit"},
    }]}
    je_b = {"DocNumber": "A2XUS-01Aug-03Aug-200", "Line": [{
        "Description": "ProductSalesNotTaxed  - OO-OO-ORG-500 - Online store",
        "Amount": 5.0,
        "JournalEntryLineDetail": {"PostingType": "Credit"},
    }]}
    result = compute_sku_revenue([je_a, je_b], _SKU_REGISTRY)
    assert result["OO-OO-ORG-500"]["revenue"] == 15.0


def test_compute_sku_revenue_empty_input_gives_empty_dict():
    assert compute_sku_revenue([], _SKU_REGISTRY) == {}


def test_compute_sku_revenue_ignores_non_a2x_journal_entries():
    """A TikTok/LinkMyBooks (or any non-A2X) entry must never contribute here, even if its
    Description text happens to match the "TYPE - SKU - suffix" shape by coincidence - only
    A2XSH-/A2XUS- DocNumber prefixes are trusted as this parser's actual source format."""
    non_a2x_je = {"DocNumber": "LMB-TT-01Aug-200", "Line": [{
        "Description": "ProductSalesNotTaxed  - OO-OO-ORG-500 - TikTok Shop",
        "Amount": 999.0,
        "JournalEntryLineDetail": {"PostingType": "Credit"},
    }]}
    assert compute_sku_revenue([non_a2x_je], _SKU_REGISTRY) == {}


def test_compute_sku_revenue_ignores_journal_entry_with_no_docnumber():
    no_doc_je = {"Line": [{
        "Description": "ProductSalesNotTaxed  - OO-OO-ORG-500 - Online store",
        "Amount": 10.0,
        "JournalEntryLineDetail": {"PostingType": "Credit"},
    }]}
    assert compute_sku_revenue([no_doc_je], _SKU_REGISTRY) == {}
