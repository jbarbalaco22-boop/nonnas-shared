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
    """Returns {net_sales, cogs, three_pl, ads, fees, contribution, contribution_pct} for one
    channel (channel is a display name — "DTC", "TikTok", "Amazon", or "Wholesale").

    net_sales/cogs/three_pl are computed by filtering on the QBO Class tag: net_sales via the
    existing per-channel rules, cogs/three_pl by checking each account is actually tagged with
    this channel's Class — both are known to post that way in practice (COGS correction entries
    and the 3PL/pick-and-pack allocation both carry a real Class tag). ads does NOT get tagged
    by Class (confirmed empirically — see ads.note in the account map), so it's allocated via
    ads.by_channel instead, a per-account platform mapping; ads.unallocated accounts are
    deliberately excluded here rather than guessed at. fees uses the existing per-channel fees
    mapping (already account-based, no Class tag needed since each fee account is
    platform-specific already).

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

    contribution = net_sales - cogs - three_pl - ads - fees

    return {
        "net_sales": net_sales,
        "cogs": cogs,
        "three_pl": three_pl,
        "ads": ads,
        "fees": fees,
        "contribution": contribution,
        "contribution_pct": (contribution / net_sales) if net_sales else None,
    }
