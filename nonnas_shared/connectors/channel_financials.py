"""Applies config/qbo_account_map.json's channel rules to a flat P&L-by-class
pull (from qbo_client.fetch_profit_and_loss_by_class) to compute net sales
and expense totals. This is the one place both projects should compute
"revenue by channel" so the number means the same thing everywhere.
"""
from nonnas_shared.config import load_qbo_account_map


def _matches(account_label: str, name_or_prefix: str) -> bool:
    return account_label == name_or_prefix or account_label.startswith(name_or_prefix)


def _sum_accounts(pl_data: dict, names: list[str], tagged_class: str | None) -> float:
    if not names:
        return 0.0
    total = 0.0
    for section in pl_data.values():
        for label, class_values in section.items():
            if any(_matches(label, name) for name in names):
                if tagged_class:
                    total += class_values.get(tagged_class, 0.0)
                else:
                    total += sum(class_values.values())
    return total


def sum_section(pl_data: dict, section: str) -> float:
    """Sums every account's total (across all Class columns) within one top-level P&L section
    (e.g. "Income", "COGS", "Expenses", "OtherIncome"). Unlike _sum_accounts, this doesn't take
    an explicit account name list - it picks up whatever QBO reports under that section, so a
    new G&A account added in QBO is captured automatically without an account_map update.
    Returns 0.0 if the section isn't present in this pull (e.g. no Other Income that period).
    """
    section_data = pl_data.get(section, {})
    return sum(sum(class_values.values()) for class_values in section_data.values())


def compute_net_income(pl_data: dict) -> dict:
    """Returns {income, cogs, expenses, other_income, net_income} - the company-wide bottom
    line, computed structurally from the P&L's own section totals (Income - COGS - Expenses +
    Other Income) rather than re-derived from channel-level attribution. This is deliberate:
    it guarantees net_income always reconciles to QBO's own Net Income line regardless of how
    complete the channel-level ads/fees/other_marketing allocation is (unallocated ad spend,
    Not-Specified-tagged 3PL, and every G&A account not in the account map all still land here
    correctly, without needing their own explicit account list).
    """
    income = sum_section(pl_data, "Income")
    cogs = sum_section(pl_data, "COGS")
    expenses = sum_section(pl_data, "Expenses")
    other_income = sum_section(pl_data, "OtherIncome") - sum_section(pl_data, "OtherExpenses")
    return {
        "income": income,
        "cogs": cogs,
        "expenses": expenses,
        "other_income": other_income,
        "net_income": income - cogs - expenses + other_income,
    }


def compute_net_sales_by_channel(pl_data: dict, account_map: dict | None = None) -> dict:
    """Returns {'net_sales': {channel: amount}, 'warnings': [str, ...]}.

    A warning is emitted (instead of guessing) whenever a rule specifies
    subtract_tagged_class without an explicit subtract account list — the
    account map is ambiguous there, and silently skipping or silently
    summing everything would both risk misstating revenue.
    """
    account_map = account_map or load_qbo_account_map()
    results = {}
    warnings = []

    for channel, rules in account_map.get("net_sales", {}).items():
        include = rules.get("include", [])
        include_class = rules.get("include_tagged_class")
        subtract = rules.get("subtract", [])
        subtract_class = rules.get("subtract_tagged_class")

        gross = _sum_accounts(pl_data, include, include_class)

        if subtract:
            # QBO reports contra-revenue accounts (discounts/refunds) as negative
            # numbers within the Income section, so adding them reduces net sales.
            deduction = _sum_accounts(pl_data, subtract, subtract_class)
        elif subtract_class:
            deduction = 0.0
            warnings.append(
                f"{channel}: subtract_tagged_class='{subtract_class}' is set but there's no "
                "explicit 'subtract' account list in qbo_account_map.json — skipping the "
                "deduction, so net sales may be overstated until the map is filled in."
            )
        else:
            deduction = 0.0

        results[channel] = gross + deduction

    return {"net_sales": results, "warnings": warnings}


def compute_expense_totals(pl_data: dict, account_map: dict | None = None) -> dict:
    """Returns {'cogs': amount, 'three_pl': amount, 'ads': amount} — company-wide,
    not split by channel (per the account map, none of the ad accounts are tagged by
    Class today — see the account map's ads.note — so this can't be split by Class the
    way net_sales can; use compute_channel_margin for a per-channel breakdown instead).
    """
    account_map = account_map or load_qbo_account_map()
    return {
        "cogs": _sum_accounts(pl_data, account_map.get("cogs", []), None),
        "three_pl": _sum_accounts(pl_data, account_map.get("three_pl", []), None),
        "ads": _sum_accounts(pl_data, account_map.get("ads", {}).get("accounts", []), None),
    }


def compute_overhead_by_account(pl_data: dict, account_map: dict | None = None) -> list[dict]:
    """Returns [{"label": account_name, "amount": total}], sorted by amount descending, for
    every Expenses-section account NOT already claimed by a channel-level bucket (cogs,
    three_pl, ads, fees, other_marketing) - i.e. the same set of accounts that make up the
    residual "Overhead" figure used elsewhere (Net Income = Contribution - Overhead), broken
    out per account instead of collapsed into one number. This is what "recurring fixed costs"
    means for this business: payroll, payroll taxes, benefits, software, legal, accounting,
    bank fees, and the handful of marketing-adjacent accounts the business chose to keep out of
    channel-level contribution (see qbo_account_map.json's excluded_from_v1).

    Cross-validated against the residual for a live 90-day pull (2026-08-18): this function's
    sum (60,568.00) landed within ~3% of contribution-minus-net-income (62,472.97) - the gap is
    Other Income/Expense section activity, which this function deliberately excludes since
    those aren't Expenses-section line items and don't belong in a "typical monthly cost" read.
    """
    account_map = account_map or load_qbo_account_map()
    claimed: set[str] = set()
    claimed.update(account_map.get("cogs", []))
    claimed.update(account_map.get("three_pl", []))
    claimed.update(account_map.get("ads", {}).get("accounts", []))
    for accounts in account_map.get("fees", {}).values():
        claimed.update(accounts if isinstance(accounts, list) else [accounts])
    for accounts in account_map.get("other_marketing", {}).get("by_channel", {}).values():
        claimed.update(accounts)

    results = []
    for label, class_values in pl_data.get("Expenses", {}).items():
        if any(_matches(label, c) for c in claimed):
            continue
        total = sum(class_values.values())
        if total:
            results.append({"label": label, "amount": total})
    results.sort(key=lambda r: r["amount"], reverse=True)
    return results


# Discounts and refunds/returns post to these two shared accounts for DTC/TikTok/Amazon
# (tagged by Class - confirmed live against a real March 2026 P&L pull, 2026-08-18), unlike
# Wholesale which uses its own "41140 Wholesale Markdown / Allowances" account instead.
_DISCOUNT_ACCOUNT_PREFIX = "43100 Discounts & Promotions"
_REFUND_ACCOUNT_PREFIX = "43200 Returns & Refunds"


def compute_channel_margin(pl_data: dict, channel: str, account_map: dict | None = None) -> dict:
    """Returns {net_sales, cogs, three_pl, ads, fees, other_marketing, contribution,
    contribution_pct, discount_rate, refund_rate} for one channel (channel is a display name —
    "DTC", "TikTok", "Amazon", or "Wholesale").

    net_sales/cogs/three_pl are computed by filtering on the QBO Class tag: net_sales via the
    existing per-channel rules, cogs/three_pl by checking each account is actually tagged with
    this channel's Class — both are known to post that way in practice (COGS correction entries
    and the 3PL/pick-and-pack allocation both carry a real Class tag). ads does NOT get tagged
    by Class (confirmed empirically — see ads.note in the account map), so it's allocated via
    ads.by_channel instead, a per-account platform mapping; ads.unallocated accounts are
    deliberately excluded here rather than guessed at. fees uses the existing per-channel fees
    mapping (already account-based, no Class tag needed since each fee account is
    platform-specific already).

    other_marketing is real per-channel cost (e.g. Email & SMS Marketing tooling) that reduces
    contribution but is kept separate from ads on purpose — mixing it into `ads` would distort
    ROAS (net_sales / ads), which is meant to measure working paid-media efficiency, not
    marketing/retention tooling spend. See other_marketing.note in the account map.

    discount_rate/refund_rate are computed from the discount/refund accounts directly (gross
    revenue is the channel's own "include" accounts, before any deduction) - deliberately QBO
    account-based, not Shopify order-data-based, so these have the same full historical depth as
    net_sales/ROAS. Real audit finding, 2026-08-18: the previous Shopify-order-based calculation
    had NO data at all for the earliest ~3 months of a 6-month trend window, since Shopify's own
    order API can't retrieve orders older than ~55 days - but the underlying QBO accounts have
    full history, going back as far as net_sales itself does. None (not 0.0) for Wholesale,
    which doesn't use these shared accounts (see _DISCOUNT_ACCOUNT_PREFIX's comment) - a 0.0%
    Wholesale discount rate from an account with zero activity would be misleadingly precise.

    contribution_pct is None (not 0.0) when net_sales is zero, so callers can distinguish
    "no revenue this period" from "revenue exactly offset by costs".
    """
    account_map = account_map or load_qbo_account_map()
    channel_key = channel.lower()

    net_sales = compute_net_sales_by_channel(pl_data, account_map)["net_sales"].get(channel_key, 0.0)
    cogs = _sum_accounts(pl_data, account_map.get("cogs", []), tagged_class=channel)
    three_pl = _sum_accounts(pl_data, account_map.get("three_pl", []), tagged_class=channel)
    ads_accounts = account_map.get("ads", {}).get("by_channel", {}).get(channel, [])
    ads = _sum_accounts(pl_data, ads_accounts, tagged_class=None)
    fees_accounts = account_map.get("fees", {}).get(channel_key, [])
    fees = _sum_accounts(pl_data, fees_accounts, tagged_class=None)
    other_marketing_accounts = account_map.get("other_marketing", {}).get("by_channel", {}).get(channel, [])
    other_marketing = _sum_accounts(pl_data, other_marketing_accounts, tagged_class=None)

    contribution = net_sales - cogs - three_pl - ads - fees - other_marketing

    gross = discount_amount = refund_amount = 0.0
    discount_rate = refund_rate = None
    if channel != "Wholesale":
        net_sales_rules = account_map.get("net_sales", {}).get(channel_key, {})
        gross = _sum_accounts(pl_data, net_sales_rules.get("include", []), net_sales_rules.get("include_tagged_class"))
        if gross:
            # QBO stores these as negative (contra-revenue) - negate for a positive rate.
            discount_amount = -_sum_accounts(pl_data, [_DISCOUNT_ACCOUNT_PREFIX], channel)
            refund_amount = -_sum_accounts(pl_data, [_REFUND_ACCOUNT_PREFIX], channel)
            discount_rate = discount_amount / gross
            refund_rate = refund_amount / gross

    return {
        "net_sales": net_sales,
        "cogs": cogs,
        "three_pl": three_pl,
        "ads": ads,
        "fees": fees,
        "other_marketing": other_marketing,
        "contribution": contribution,
        "contribution_pct": (contribution / net_sales) if net_sales else None,
        "discount_rate": discount_rate,
        "refund_rate": refund_rate,
        # Raw dollar figures behind discount_rate/refund_rate, for company_totals to sum and
        # re-derive a properly weighted company-wide rate from (not an average of per-channel
        # rates - see compute_company_totals' docstring for why that distinction matters).
        "gross_revenue": gross,
        "discount_amount": discount_amount,
        "refund_amount": refund_amount,
    }


def compute_channel_health_metrics(channel_margin: dict, shopify_totals: dict) -> dict:
    """Returns {aov, discount_rate, refund_rate, roas} for one channel — retail health ratios
    combining QBO-derived margin data (net_sales, ads, discount_rate, refund_rate) with
    Shopify-derived order data (orders, net_revenue).

    channel_margin: one channel's dict as returned by compute_channel_margin.
    shopify_totals: one channel's dict as returned by handlers.get_channel_units_live, i.e.
    {orders, gross, discounts, refunds, net_revenue, ...}.

    discount_rate/refund_rate are pass-through from channel_margin (QBO account-based, full
    historical depth) rather than recomputed from shopify_totals - real audit finding,
    2026-08-18: the old Shopify-order-based calculation had no data at all for months outside
    Shopify's ~55-day live-retrievable order window, even though the underlying QBO discount/
    refund accounts have full history. AOV still needs Shopify's order count (QBO has no
    per-order granularity), so it keeps the same limitation.

    Every ratio is None (not 0.0) when its denominator is zero — "no orders this period" and
    "AOV of exactly $0" are different things, and callers need to be able to tell them apart
    rather than silently rendering a misleading zero.
    """
    orders = shopify_totals.get("orders", 0)
    net_revenue = shopify_totals.get("net_revenue", 0.0)
    ads = channel_margin.get("ads", 0.0)
    net_sales = channel_margin.get("net_sales", 0.0)

    return {
        "aov": (net_revenue / orders) if orders else None,
        "discount_rate": channel_margin.get("discount_rate"),
        "refund_rate": channel_margin.get("refund_rate"),
        "roas": (net_sales / ads) if ads else None,
    }


def compute_company_totals(channel_margins: dict, shopify_totals_by_channel: dict) -> dict:
    """Returns the company-wide equivalent of one channel card — same shape as
    compute_channel_margin merged with compute_channel_health_metrics (net_sales, cogs,
    three_pl, ads, fees, other_marketing, contribution, contribution_pct, orders, units, aov,
    discount_rate, refund_rate, roas), summed/recomputed across every channel in the given
    dicts rather than scoped to one. Ratios (aov/discount_rate/refund_rate/roas) are recomputed
    from the summed numerators/denominators, not averaged per-channel, for the same reason a
    blended average needs weighting - e.g. AOV is total net revenue over total orders, not the
    mean of four channel AOVs.

    channel_margins: {channel: compute_channel_margin(...) result}.
    shopify_totals_by_channel: {channel: handlers.get_channel_units_live(...)["channels"][ch]}.
    """
    net_sales = sum(m.get("net_sales", 0.0) for m in channel_margins.values())
    cogs = sum(m.get("cogs", 0.0) for m in channel_margins.values())
    three_pl = sum(m.get("three_pl", 0.0) for m in channel_margins.values())
    ads = sum(m.get("ads", 0.0) for m in channel_margins.values())
    fees = sum(m.get("fees", 0.0) for m in channel_margins.values())
    other_marketing = sum(m.get("other_marketing", 0.0) for m in channel_margins.values())
    contribution = sum(m.get("contribution", 0.0) for m in channel_margins.values())

    orders = sum(s.get("orders", 0) for s in shopify_totals_by_channel.values())
    units = sum(s.get("units", 0) for s in shopify_totals_by_channel.values())
    net_revenue = sum(s.get("net_revenue", 0.0) for s in shopify_totals_by_channel.values())
    # discount_rate/refund_rate: summed dollar amounts from channel_margins (QBO account-based,
    # full historical depth), not shopify_totals_by_channel's gross/discounts/refunds - see
    # compute_channel_margin's docstring for why.
    gross = sum(m.get("gross_revenue", 0.0) for m in channel_margins.values())
    discount_amount = sum(m.get("discount_amount", 0.0) for m in channel_margins.values())
    refund_amount = sum(m.get("refund_amount", 0.0) for m in channel_margins.values())

    return {
        "net_sales": net_sales,
        "cogs": cogs,
        "three_pl": three_pl,
        "ads": ads,
        "fees": fees,
        "other_marketing": other_marketing,
        "contribution": contribution,
        "contribution_pct": (contribution / net_sales) if net_sales else None,
        "orders": orders,
        "units": units,
        "aov": (net_revenue / orders) if orders else None,
        "discount_rate": (discount_amount / gross) if gross else None,
        "refund_rate": (refund_amount / gross) if gross else None,
        "roas": (net_sales / ads) if ads else None,
    }


def compute_revenue_concentration(all_channel_margins: dict) -> dict:
    """Returns {channel: share_of_total_net_sales} across every channel in the given dict, each
    a fraction (0.42, not 42) so a channel dominating revenue is easy to spot. All zeros if
    there's no revenue in the period at all, rather than dividing by zero."""
    total = sum(m.get("net_sales", 0.0) for m in all_channel_margins.values())
    if not total:
        return {ch: 0.0 for ch in all_channel_margins}
    return {ch: m.get("net_sales", 0.0) / total for ch, m in all_channel_margins.items()}
