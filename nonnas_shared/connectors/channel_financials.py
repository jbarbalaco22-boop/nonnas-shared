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


def compute_channel_margin(pl_data: dict, channel: str, account_map: dict | None = None) -> dict:
    """Returns {net_sales, cogs, three_pl, ads, fees, other_marketing, contribution,
    contribution_pct} for one channel (channel is a display name — "DTC", "TikTok", "Amazon",
    or "Wholesale").

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

    return {
        "net_sales": net_sales,
        "cogs": cogs,
        "three_pl": three_pl,
        "ads": ads,
        "fees": fees,
        "other_marketing": other_marketing,
        "contribution": contribution,
        "contribution_pct": (contribution / net_sales) if net_sales else None,
    }


def compute_channel_health_metrics(channel_margin: dict, shopify_totals: dict) -> dict:
    """Returns {aov, discount_rate, refund_rate, roas} for one channel — retail health ratios
    combining QBO-derived margin data (net_sales, ads) with Shopify-derived order data (orders,
    gross, discounts, refunds).

    channel_margin: one channel's dict as returned by compute_channel_margin.
    shopify_totals: one channel's dict as returned by handlers.get_channel_units_live, i.e.
    {orders, gross, discounts, refunds, net_revenue, ...}.

    Every ratio is None (not 0.0) when its denominator is zero — "no orders this period" and
    "AOV of exactly $0" are different things, and callers need to be able to tell them apart
    rather than silently rendering a misleading zero.
    """
    orders = shopify_totals.get("orders", 0)
    gross = shopify_totals.get("gross", 0.0)
    discounts = shopify_totals.get("discounts", 0.0)
    refunds = shopify_totals.get("refunds", 0.0)
    net_revenue = shopify_totals.get("net_revenue", 0.0)
    ads = channel_margin.get("ads", 0.0)
    net_sales = channel_margin.get("net_sales", 0.0)

    return {
        "aov": (net_revenue / orders) if orders else None,
        "discount_rate": (discounts / gross) if gross else None,
        "refund_rate": (refunds / gross) if gross else None,
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
    gross = sum(s.get("gross", 0.0) for s in shopify_totals_by_channel.values())
    discounts = sum(s.get("discounts", 0.0) for s in shopify_totals_by_channel.values())
    refunds = sum(s.get("refunds", 0.0) for s in shopify_totals_by_channel.values())
    net_revenue = sum(s.get("net_revenue", 0.0) for s in shopify_totals_by_channel.values())

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
        "discount_rate": (discounts / gross) if gross else None,
        "refund_rate": (refunds / gross) if gross else None,
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
