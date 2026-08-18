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

Pre-SKU-posting history: A2X only started embedding SKU in the Description text once SKU-level
revenue posting was turned on in the connector settings (2026-08-ish). Entries from before that
have no SKU segment at all - just "ProductSalesNotTaxed  - Online store", two segments instead
of three. Since OO-OO-ORG-500 was the sole real, active SKU for all of that history (per
sku_map.json), any SKU-attributable line with no SKU segment present defaults to the registry's
one "active" SKU, if there's exactly one - see _parse_line_description's "no segment at all" vs
"an unrecognized SKU-shaped segment" distinction below; only the former defaults. Once a second
SKU goes active, this default turns itself off automatically (ambiguous who a segment-less line
belongs to) rather than needing a manual date cutoff.

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
    """Parses one JournalEntry line's free-text Description into {type, sku, suffix, no_sku_segment}.

    The middle segment is only treated as a SKU if it's a real, registered SKU code (from
    nonnas_shared.config.load_sku_map) - anything else is left unresolved rather than guessed
    from the string's shape alone. Two different "unresolved" cases are distinguished, because
    only one of them is safe to default:

    - no_sku_segment=True: the description has no candidate SKU segment at all (old-format, two
      segments - "TYPE  - Online store" - from before A2X started embedding SKU in the text).
      Safe to attribute to a sole active SKU, if there is exactly one.
    - no_sku_segment=False with sku=None: there IS a middle segment, it's just not a recognized
      SKU (e.g. a brand-new SKU not yet added to sku_map.json, or something unexpected). Never
      guessed - could genuinely be a different, unregistered product.
    """
    m = _DESC_PATTERN.match(description or "")
    if not m:
        return {"type": None, "sku": None, "suffix": description, "no_sku_segment": True}
    txn_type = m.group("type")
    rest = m.group("rest")
    parts = rest.split(" - ", 1)
    if len(parts) == 2:
        if parts[0].strip() in known_skus:
            return {"type": txn_type, "sku": parts[0].strip(), "suffix": parts[1].strip(), "no_sku_segment": False}
        return {"type": txn_type, "sku": None, "suffix": rest, "no_sku_segment": False}
    return {"type": txn_type, "sku": None, "suffix": rest, "no_sku_segment": True}


def _sole_active_sku(sku_registry: dict) -> str | None:
    active = [sku for sku, info in sku_registry.items() if info.get("status") == "active"]
    return active[0] if len(active) == 1 else None


def compute_sku_revenue(journal_entries: list, sku_registry: dict) -> dict:
    """Aggregates SKU-level revenue and discounts from raw JournalEntry transactions (as
    returned by qbo_client.fetch_journal_entries).

    Returns {sku: {"name": str|None, "revenue": float, "discounts": float, "net": float}}.
    discounts is negative (matching the sign convention used everywhere else in this codebase -
    QBO's own discount/refund accounts carry negative values within Income), so net = revenue +
    discounts, not revenue - discounts. Sign is derived from each line's real PostingType
    (Credit/Debit) rather than assumed from the line type, so it stays correct even if a
    correction or reversal entry ever posts with the "wrong" direction for its type.

    Lines with no SKU segment at all (pre-SKU-posting history - see this module's docstring)
    default to the registry's sole "active" SKU when there's exactly one. Once a second SKU goes
    active this stops happening automatically - a segment-less line is then genuinely ambiguous.
    """
    known_skus = set(sku_registry.keys())
    fallback_sku = _sole_active_sku(sku_registry)
    result: dict = {}

    for je in journal_entries:
        doc_number = je.get("DocNumber", "") or ""
        if not doc_number.startswith(_A2X_DOCNUMBER_PREFIXES):
            continue
        for line in je.get("Line", []):
            detail = line.get("JournalEntryLineDetail", {})
            parsed = _parse_line_description(line.get("Description", ""), known_skus)
            if parsed["type"] not in _SKU_ATTRIBUTABLE_TYPES:
                continue

            sku = parsed["sku"]
            if sku is None:
                if not parsed["no_sku_segment"] or fallback_sku is None:
                    continue
                sku = fallback_sku

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
