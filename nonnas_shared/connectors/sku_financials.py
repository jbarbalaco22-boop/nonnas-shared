"""Recovers SKU-level revenue, discounts, and refunds from raw QBO JournalEntry transactions.

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

Which lines count: by QBO account, not by the free-text type string. Originally this matched a
hardcoded set of type strings ({"ProductSalesNotTaxed", "DiscountNotTaxed"}), which silently
missed real revenue: A2X also posts a "ProductSales" type (no "NotTaxed" suffix) for orders where
sales tax applied - same real revenue, different label, worth $177.14 in one real July 2026 test
that surfaced this reconciling against DTC's own Net Sales figure. Matching by destination
account instead (the same accounts channel_financials.py already treats as DTC's revenue/discount/
refund buckets - see qbo_account_map.json's "net_sales.dtc" section) is robust to new type strings
A2X might introduce, since the destination account for real product revenue/discounts/refunds
doesn't change. Shipping Revenue is deliberately excluded even though it's part of DTC Net Sales -
it isn't tied to a specific SKU.

Pre-SKU-posting history: A2X only started embedding SKU in the Description text once SKU-level
revenue posting was turned on in the connector settings (2026-08-ish). Entries from before that
have no SKU segment at all - just "ProductSalesNotTaxed  - Online store", two segments instead
of three. Since OO-OO-ORG-500 was the sole real, active SKU for all of that history (per
sku_map.json), any revenue/discount/refund line with no SKU segment present defaults to the
registry's one "active" SKU, if there's exactly one - see _parse_line_description's "no segment
at all" vs "an unrecognized SKU-shaped segment" distinction below; only the former defaults. Once
a second SKU goes active, this default turns itself off automatically (ambiguous who a
segment-less line belongs to) rather than needing a manual date cutoff.

Known residual gap: a line shaped "TYPE  - Online store - some_reason" (three segments, but the
middle one is "Online store"/a checkout type rather than a SKU - seen on
RefundAdjustmentNotTaxed lines) is treated as "an unrecognized SKU-shaped segment", not "no
segment at all", so it does NOT get the sole-active-SKU default even though in practice it's
unambiguous today. This is deliberately conservative (never guess when a segment could
plausibly be a real, different, not-yet-registered SKU) and shows up as a few dollars of
uncaptured refunds - small enough to accept rather than special-case.

IMPORTANT scope limit: this format is specific to A2X (Shopify + native Amazon). TikTok posts
through a different connector (LinkMyBooks) with its own Description format, which today just
fails to match and gets silently excluded - safe, but that's incidental, not guaranteed. To make
that guarantee explicit rather than relying on a regex accidentally not matching, only journal
entries whose DocNumber carries an A2X prefix (A2XSH- for Shopify, A2XUS- for native Amazon; see
nonnas-shared/CLAUDE.md's "A2X / Shopify structural gap" section) are considered at all. On top of
that, the discount/refund accounts are shared across channels (Amazon posts to the same
"Discounts & Promotions"/"Returns & Refunds" account names, just tagged Class=Amazon) - so those
two are further scoped to ClassRef=DTC. The revenue account name itself already encodes the
channel ("Product Revenue - DTC" vs "Product Revenue - Amazon"), so no extra Class check is
needed there, but Class=DTC is checked for it too, for defense in depth.
"""
import re

_DESC_PATTERN = re.compile(r"^(?P<type>\S+)\s*-\s*(?P<rest>.+)$")
_A2X_DOCNUMBER_PREFIXES = ("A2XSH-", "A2XUS-")

# Destination QBO account names (as they appear on JournalEntryLineDetail.AccountRef.name, which
# omits the leading account number that qbo_account_map.json's report-oriented labels carry) that
# feed DTC's SKU-attributable revenue/discounts/refunds. Mirrors qbo_account_map.json's
# "net_sales.dtc" bucket (Shipping Revenue excluded - not SKU-specific). If a new account is ever
# added to that bucket, mirror it here too.
_REVENUE_ACCOUNTS = {"Product Revenue – DTC", "Merch Revenue"}
_DISCOUNT_ACCOUNTS = {"Discounts & Promotions"}
_REFUND_ACCOUNTS = {"Returns & Refunds", "Chargebacks"}


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


def _line_role(account_name: str, class_name: str) -> str | None:
    """Returns "revenue", "discount", "refund", or None (not SKU-attributable), based on the
    line's destination account - see this module's docstring for why account rather than the
    free-text type string, and why Class=DTC is required for the two shared accounts."""
    if account_name in _REVENUE_ACCOUNTS and class_name == "DTC":
        return "revenue"
    if class_name != "DTC":
        return None
    if account_name in _DISCOUNT_ACCOUNTS:
        return "discount"
    if account_name in _REFUND_ACCOUNTS:
        return "refund"
    return None


def compute_sku_revenue(journal_entries: list, sku_registry: dict) -> dict:
    """Aggregates SKU-level revenue, discounts, and refunds from raw JournalEntry transactions
    (as returned by qbo_client.fetch_journal_entries).

    Returns {sku: {"name": str|None, "revenue": float, "discounts": float, "refunds": float,
    "net": float}}. discounts and refunds are negative (matching the sign convention used
    everywhere else in this codebase), so net = revenue + discounts + refunds. Sign is derived
    from each line's real PostingType (Credit/Debit) rather than assumed from the account, so it
    stays correct even if a correction or reversal entry ever posts with the "wrong" direction.

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
            account_name = detail.get("AccountRef", {}).get("name", "")
            class_name = detail.get("ClassRef", {}).get("name", "")
            role = _line_role(account_name, class_name)
            if role is None:
                continue

            parsed = _parse_line_description(line.get("Description", ""), known_skus)
            sku = parsed["sku"]
            if sku is None:
                if not parsed["no_sku_segment"] or fallback_sku is None:
                    continue
                sku = fallback_sku

            entry = result.setdefault(sku, {
                "name": sku_registry.get(sku, {}).get("name"),
                "revenue": 0.0,
                "discounts": 0.0,
                "refunds": 0.0,
            })
            amount = line.get("Amount", 0.0)
            signed = amount if detail.get("PostingType") == "Credit" else -amount
            if role == "revenue":
                entry["revenue"] += signed
            elif role == "discount":
                entry["discounts"] += signed
            else:
                entry["refunds"] += signed

    for entry in result.values():
        entry["net"] = entry["revenue"] + entry["discounts"] + entry["refunds"]
    return result
