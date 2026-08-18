"""Recovers SKU-level revenue and discounts from raw QBO JournalEntry transactions.

Confirmed against a real test entry (JournalEntry ID 3887, DocNumber A2XSH-10Aug-12Aug-281,
2026-08-18): A2X posts every transaction as a JournalEntry, and QBO's JournalEntry line schema
has no Item/Product-Service field at all - it structurally cannot carry one, regardless of
connector configuration (see qbo_client.fetch_profit_and_loss_by_product's docstring for the
full story). A2X embeds the SKU as plain text in each line's Description instead, e.g.:

    "ProductSalesNotTaxed  - OO-OO-ORG-500 - Online store"
    "DiscountNotTaxed  - OO-OO-ORG-500 - subscription_contract_checkout_one"
    "ShippingNotTaxed  - Online store"                        (no SKU - not product-specific)

This is the only place that should parse that description format - if A2X changes it, this is
the one spot to fix.

IMPORTANT scope limit: this format is specific to A2X (Shopify + native Amazon). TikTok posts
through a different connector (LinkMyBooks) with its own Description format, which today just
fails to match and gets silently excluded - safe, but that's incidental, not guaranteed. To make
that guarantee explicit rather than relying on a regex accidentally not matching, only journal
entries whose DocNumber carries an A2X prefix (A2XSH- for Shopify, A2XUS- for native Amazon; see
nonnas-shared/CLAUDE.md's "A2X / Shopify structural gap" section) are considered at all. If a
non-A2X connector's Description ever happens to match this same "TYPE - SKU - suffix" shape, it
still won't leak into these totals.
"""
import re

_DESC_PATTERN = re.compile(r"^(?P<type>\S+)\s*-\s*(?P<rest>.+)$")
_A2X_DOCNUMBER_PREFIXES = ("A2XSH-", "A2XUS-")

# Only these line types get attributed to a SKU. COGS, shipping, fees, and settlement-balance
# lines never carry SKU detail in this connector's output (confirmed - COGS posts as separate,
# unSKU'd bulk journal entries, e.g. "Cost of Goods Sold by other").
_SKU_ATTRIBUTABLE_TYPES = {"ProductSalesNotTaxed", "DiscountNotTaxed"}


def _parse_line_description(description: str, known_skus: set) -> dict:
    """Parses one JournalEntry line's free-text Description into {type, sku, suffix}.

    The middle segment is only treated as a SKU if it's a real, registered SKU code (from
    nonnas_shared.config.load_sku_map) - anything else (e.g. "Online store") is treated as part
    of the suffix with no SKU, rather than guessing from the string's shape alone. This means a
    brand-new SKU not yet added to the registry won't be recognized here either - add it to
    sku_map.json first.
    """
    m = _DESC_PATTERN.match(description or "")
    if not m:
        return {"type": None, "sku": None, "suffix": description}
    txn_type = m.group("type")
    rest = m.group("rest")
    parts = rest.split(" - ", 1)
    if len(parts) == 2 and parts[0].strip() in known_skus:
        return {"type": txn_type, "sku": parts[0].strip(), "suffix": parts[1].strip()}
    return {"type": txn_type, "sku": None, "suffix": rest}


def compute_sku_revenue(journal_entries: list, sku_registry: dict) -> dict:
    """Aggregates SKU-level revenue and discounts from raw JournalEntry transactions (as
    returned by qbo_client.fetch_journal_entries).

    Returns {sku: {"name": str|None, "revenue": float, "discounts": float, "net": float}}.
    discounts is negative (matching the sign convention used everywhere else in this codebase -
    QBO's own discount/refund accounts carry negative values within Income), so net = revenue +
    discounts, not revenue - discounts. Sign is derived from each line's real PostingType
    (Credit/Debit) rather than assumed from the line type, so it stays correct even if a
    correction or reversal entry ever posts with the "wrong" direction for its type.
    """
    known_skus = set(sku_registry.keys())
    result: dict = {}

    for je in journal_entries:
        doc_number = je.get("DocNumber", "") or ""
        if not doc_number.startswith(_A2X_DOCNUMBER_PREFIXES):
            continue
        for line in je.get("Line", []):
            detail = line.get("JournalEntryLineDetail", {})
            parsed = _parse_line_description(line.get("Description", ""), known_skus)
            if parsed["sku"] is None or parsed["type"] not in _SKU_ATTRIBUTABLE_TYPES:
                continue

            sku = parsed["sku"]
            entry = result.setdefault(sku, {
                "name": sku_registry.get(sku, {}).get("name"),
                "revenue": 0.0,
                "discounts": 0.0,
            })
            amount = line.get("Amount", 0.0)
            signed = amount if detail.get("PostingType") == "Credit" else -amount
            if parsed["type"] == "ProductSalesNotTaxed":
                entry["revenue"] += signed
            else:
                entry["discounts"] += signed

    for entry in result.values():
        entry["net"] = entry["revenue"] + entry["discounts"]
    return result
