"""Raw data pulls from QuickBooks Online.

fetch_profit_and_loss_by_class returns account-line-level detail (not just
section totals), so callers can apply config/qbo_account_map.json's
include/subtract rules to compute net sales, COGS, fees, etc. per channel —
see channel_financials.py for that layer.
"""
import re
from datetime import date

from nonnas_shared.connectors.http import request as http_request
from nonnas_shared.connectors.qbo_auth import api_base_url

_LEADING_ACCOUNT_NUMBER = re.compile(r"^\d+\s+")


def _get(realm_id: str, access_token: str, environment: str, path: str, params: dict) -> dict:
    url = f"{api_base_url(environment)}/v3/company/{realm_id}/{path}"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    resp = http_request("GET", url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_profit_and_loss_by_class(
    realm_id: str, access_token: str, start_date: date, end_date: date, environment: str = "production"
) -> dict:
    """Return {section: {account_label: {class_name: amount}}} for every account
    line in the P&L, e.g. {"Income": {"41100 Product Revenue - DTC": {"DTC": 1234.56}}}.
    """
    report = _get(realm_id, access_token, environment, "reports/ProfitAndLoss", {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "summarize_column_by": "Classes",
        "minorversion": 65,
    })

    columns = report.get("Columns", {}).get("Column", [])
    class_names = [c.get("ColTitle", "") for c in columns[1:]]

    sink: dict = {}
    _walk_pl_rows(report.get("Rows", {}).get("Row", []), class_names, None, sink)
    return sink


def _walk_pl_rows(rows: list, class_names: list, current_group: str | None, sink: dict) -> None:
    for row in rows:
        group = row.get("group", current_group)

        if row.get("type") == "Data":
            col_data = row.get("ColData", [])
            if not col_data:
                continue
            label = (col_data[0].get("value") or "").strip()
            if not label:
                continue
            values = {}
            for name, cell in zip(class_names, col_data[1:]):
                if name.strip().lower() == "total":
                    continue
                raw = cell.get("value", "")
                try:
                    values[name] = float(raw) if raw else 0.0
                except ValueError:
                    values[name] = 0.0
            sink.setdefault(group or "Other", {})[label] = values

        nested_rows = row.get("Rows", {}).get("Row", [])
        if nested_rows:
            _walk_pl_rows(nested_rows, class_names, group, sink)


def fetch_profit_and_loss_by_product(
    realm_id: str, access_token: str, start_date: date, end_date: date, environment: str = "production"
) -> dict:
    """Return {section: {account_label: {product_name: amount}}} - same shape as
    fetch_profit_and_loss_by_class, but grouped by Product/Service (i.e. SKU/Item) instead of
    Class. Same underlying reports/ProfitAndLoss endpoint, just a different
    summarize_column_by - confirmed working live (2026-08-18) rather than assumed from docs,
    matching how every other QBO report quirk in this codebase has been verified the hard way.

    As of 2026-08-18 this returns everything bucketed under "Not Specified", because no
    transaction line currently carries a Product/Service reference (only one SKU has ever
    existed, and its revenue/COGS post as Class-tagged summary entries with no Item detail).
    It should start returning real per-SKU columns once the new SKUs launch, since their
    Shopify-to-QBO connectors are being set up to carry Item-level detail through - but that
    real per-SKU behavior has NOT been verified yet and needs a check against the first actual
    SKU-tagged transaction once one exists.
    """
    report = _get(realm_id, access_token, environment, "reports/ProfitAndLoss", {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "summarize_column_by": "ProductsAndServices",
        "minorversion": 65,
    })

    columns = report.get("Columns", {}).get("Column", [])
    product_names = [c.get("ColTitle", "") for c in columns[1:]]

    sink: dict = {}
    _walk_pl_rows(report.get("Rows", {}).get("Row", []), product_names, None, sink)
    return sink


def fetch_account_balances(
    realm_id: str, access_token: str, account_names: list[str], environment: str = "production"
) -> dict:
    """Return {account_name: current_balance} for accounts matching the given
    names, keyed by the name as given in account_names (not QBO's real name).

    account_names may carry a leading account-number prefix (as they appear
    in P&L report row labels / qbo_account_map.json, e.g. "11100 Chase
    Operating Bank Account") even though QBO's Account.Name field doesn't
    include it, and the real Account.Name often has its own trailing suffix
    (e.g. "Chase Operating Bank Account - 9889" for the account mask). An
    exact WHERE Name IN (...) match against those never matches anything, so
    this pulls the account list and matches by prefix after stripping any
    leading account number from the target.
    """
    if not account_names:
        return {}

    targets = [(name, _LEADING_ACCOUNT_NUMBER.sub("", name)) for name in account_names]

    data = _get(realm_id, access_token, environment, "query", {
        "query": "SELECT Name, CurrentBalance FROM Account MAXRESULTS 1000",
        "minorversion": 65,
    })

    balances = {}
    for account in data.get("QueryResponse", {}).get("Account", []):
        real_name = account.get("Name", "")
        for original, stripped in targets:
            if real_name == stripped or real_name.startswith(stripped):
                balances[original] = float(account.get("CurrentBalance", 0))
    return balances


def fetch_open_bills(realm_id: str, access_token: str, environment: str = "production") -> list[dict]:
    """Return open (unpaid) AP bills with vendor, due date, and outstanding amount."""
    query = "SELECT Id, DocNumber, VendorRef, DueDate, Balance, TotalAmt FROM Bill WHERE Balance > '0'"
    data = _get(realm_id, access_token, environment, "query", {
        "query": query,
        "minorversion": 65,
    })

    bills = []
    for bill in data.get("QueryResponse", {}).get("Bill", []):
        bills.append({
            "id": bill.get("Id"),
            "doc_number": bill.get("DocNumber"),
            "vendor": bill.get("VendorRef", {}).get("name"),
            "due_date": bill.get("DueDate"),
            "balance": float(bill.get("Balance", 0)),
            "total": float(bill.get("TotalAmt", 0)),
        })
    return bills
