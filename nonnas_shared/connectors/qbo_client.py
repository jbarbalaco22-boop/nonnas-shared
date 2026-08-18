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


def _walk_gl_balance_rows(rows: list, account_prefixes: tuple, sink: list) -> None:
    for row in rows:
        header = row.get("Header", {}).get("ColData", [])
        summary = row.get("Summary", {}).get("ColData", [])
        if header and summary:
            label = (header[0].get("value") or "").strip()
            if label.startswith(account_prefixes):
                raw = summary[-2].get("value", "") if len(summary) >= 2 else ""
                # GeneralLedger's Summary row is [Total for <label>, ..., ending balance, ""] -
                # the balance is the second-to-last column, not a fixed index, since column
                # count varies by report options.
                try:
                    sink.append(float(raw) if raw else 0.0)
                except ValueError:
                    pass
        nested_rows = row.get("Rows", {}).get("Row", [])
        if nested_rows:
            _walk_gl_balance_rows(nested_rows, account_prefixes, sink)


def fetch_gl_account_balance(
    realm_id: str, access_token: str, account_prefixes: list[str], as_of_date: date,
    environment: str = "production",
) -> float:
    """Sums the ending balance (as of as_of_date) of every account whose GeneralLedger report
    label starts with one of account_prefixes - e.g. ["11100 Chase Operating Bank Account",
    "11110 Highbeam Checking", "11120 Highbeam High Yield Savings"] for combined cash on hand.

    Deliberately uses GeneralLedger with an explicit end_date, not BalanceSheet - BalanceSheet's
    end_date is documented as unreliable for a date-scoped balance (silently returns today's
    balance regardless of the date passed; see nonnas-shared/CLAUDE.md's QBO gotchas). Confirmed
    live (2026-08-18) that GeneralLedger's end_date genuinely changes the returned balance -
    pulling as of today and as of 90 days earlier gave two different, real numbers ($67,029.26 vs
    $105,758.02), cross-checked against Account.CurrentBalance for the as-of-today case (exact
    match), same trusted pattern already used for Inventory in channel_financials.py.

    start_date is fixed well before any real company data (2020-01-01) rather than taking one as
    a parameter - this is always meant to be "since account inception through as_of_date," a
    running balance, not a period-scoped sum.
    """
    report = _get(realm_id, access_token, environment, "reports/GeneralLedger", {
        "start_date": "2020-01-01",
        "end_date": as_of_date.isoformat(),
        "minorversion": 65,
    })
    balances: list = []
    _walk_gl_balance_rows(report.get("Rows", {}).get("Row", []), tuple(account_prefixes), balances)
    return sum(balances)


def _walk_gl_detail_rows(rows: list, account_prefix: str, sink: list) -> None:
    for row in rows:
        header = row.get("Header", {}).get("ColData", [])
        if header and (header[0].get("value") or "").strip().startswith(account_prefix):
            for line in row.get("Rows", {}).get("Row", []):
                col_data = line.get("ColData", [])
                if len(col_data) < 2:
                    continue
                txn_date = col_data[0].get("value", "")
                amount_raw = col_data[-2].get("value", "")
                if not txn_date or not amount_raw:
                    continue
                try:
                    sink.append({"date": txn_date, "amount": float(amount_raw)})
                except ValueError:
                    continue
            return  # found the target account's own section - no sub-accounts to recurse into
        nested_rows = row.get("Rows", {}).get("Row", [])
        if nested_rows:
            _walk_gl_detail_rows(nested_rows, account_prefix, sink)


def fetch_gl_account_transactions(
    realm_id: str, access_token: str, account_prefix: str, start_date: date, end_date: date,
    environment: str = "production",
) -> list[dict]:
    """Returns [{"date": "YYYY-MM-DD", "amount": float}] - the individual transaction lines
    posted to the one account whose GeneralLedger label starts with account_prefix, within
    [start_date, end_date]. Same GeneralLedger endpoint and column layout as
    fetch_gl_account_balance (amount is the second-to-last ColData column, balance the last),
    just reading line detail instead of the section's ending-balance Summary row.
    """
    report = _get(realm_id, access_token, environment, "reports/GeneralLedger", {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "minorversion": 65,
    })
    transactions: list = []
    _walk_gl_detail_rows(report.get("Rows", {}).get("Row", []), account_prefix, transactions)
    return transactions


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
    summarize_column_by - confirmed the API itself supports this live (2026-08-18).

    UPDATE (2026-08-18, confirmed against a real test JournalEntry, ID 3887): this will NOT
    start returning real per-SKU columns even with the new SKUs live. A2X posts every
    transaction as a JournalEntry, and QBO's JournalEntry line schema has no Item/Product-
    Service field at all - it structurally cannot carry one, regardless of connector
    configuration. A2X does embed the SKU in each line's free-text Description instead (e.g.
    "ProductSalesNotTaxed  - OO-OO-ORG-500 - Online store") - see fetch_journal_entries and
    sku_financials.compute_sku_revenue for how that's actually recovered. This function is
    left in place since the API grouping itself is real and may become useful if a connector
    ever posts Sales Receipts/Invoices instead of Journal Entries, but don't rely on it for
    SKU-level reporting against the current connector setup.
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


def fetch_journal_entries(
    realm_id: str, access_token: str, start_date: date, end_date: date, environment: str = "production"
) -> list[dict]:
    """Raw JournalEntry transactions (full Line detail included) for a date range - the only
    way to recover SKU-level revenue/discount detail from this connector's output, since
    QBO's JournalEntry schema has no Item/Product-Service field at all (see
    fetch_profit_and_loss_by_product's docstring). A2X embeds the SKU as plain text in each
    line's Description instead; see sku_financials.compute_sku_revenue for the parser.

    Paginated (QBO caps a single query response) - safe for any date range, not just short ones.
    """
    entries: list[dict] = []
    start_position = 1
    page_size = 1000
    while True:
        query = (
            f"SELECT * FROM JournalEntry WHERE TxnDate >= '{start_date.isoformat()}' "
            f"AND TxnDate <= '{end_date.isoformat()}' STARTPOSITION {start_position} MAXRESULTS {page_size}"
        )
        data = _get(realm_id, access_token, environment, "query", {"query": query, "minorversion": 65})
        batch = data.get("QueryResponse", {}).get("JournalEntry", [])
        entries.extend(batch)
        if len(batch) < page_size:
            break
        start_position += page_size
    return entries


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
