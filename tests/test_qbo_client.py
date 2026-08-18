"""Tests for qbo_client.py's P&L report row-walking — the logic that turns
QBO's nested Rows/ColData report JSON into a flat {section: {label: {class: amount}}}
structure. Uses a synthetic report shaped like QBO's real ProfitAndLoss-by-class
response rather than hitting the live API."""
from nonnas_shared.connectors.qbo_client import _walk_pl_rows


def _report_columns(*class_names):
    return [{"ColTitle": name} for name in ("", *class_names)]


def test_walks_simple_income_section():
    rows = [
        {
            "type": "Section",
            "group": "Income",
            "Header": {"ColData": [{"value": "Income"}]},
            "Rows": {"Row": [
                {"type": "Data", "ColData": [
                    {"value": "41100 Product Revenue - DTC"},
                    {"value": "1234.56"},
                ]},
            ]},
            "Summary": {"ColData": [{"value": "Total Income"}, {"value": "1234.56"}]},
        }
    ]
    sink = {}
    _walk_pl_rows(rows, ["DTC"], None, sink)
    assert sink == {"Income": {"41100 Product Revenue - DTC": {"DTC": 1234.56}}}


def test_skips_total_column():
    rows = [
        {
            "type": "Section", "group": "Income",
            "Rows": {"Row": [
                {"type": "Data", "ColData": [
                    {"value": "41100 Product Revenue - DTC"},
                    {"value": "1000.0"},
                    {"value": "1000.0"},  # the "Total" column
                ]},
            ]},
        }
    ]
    sink = {}
    _walk_pl_rows(rows, ["DTC", "Total"], None, sink)
    assert sink["Income"]["41100 Product Revenue - DTC"] == {"DTC": 1000.0}


def test_handles_nested_groups_and_empty_values():
    rows = [
        {
            "type": "Section", "group": "Expenses",
            "Rows": {"Row": [
                {
                    "type": "Section", "group": "Expenses",  # nested subsection, same group
                    "Rows": {"Row": [
                        {"type": "Data", "ColData": [
                            {"value": "52100 3PL Fulfillment Fees"},
                            {"value": ""},  # blank = no activity for this class
                            {"value": "150.0"},
                        ]},
                    ]},
                },
            ]},
        }
    ]
    sink = {}
    _walk_pl_rows(rows, ["DTC", "Amazon"], None, sink)
    assert sink["Expenses"]["52100 3PL Fulfillment Fees"] == {"DTC": 0.0, "Amazon": 150.0}


def test_skips_rows_with_no_label():
    rows = [
        {"type": "Data", "ColData": [{"value": ""}, {"value": "10.0"}]},
    ]
    sink = {}
    _walk_pl_rows(rows, ["DTC"], "Income", sink)
    assert sink == {}


# ---- GeneralLedger balance/transaction walking (for Cash & Runway - combined bank balance) ----
from nonnas_shared.connectors import qbo_client
from nonnas_shared.connectors.qbo_client import (
    _walk_gl_balance_rows,
    _walk_gl_detail_rows,
    _walk_gl_detail_rows_multi,
)


def _gl_summary_row(label, balance, nested_rows=None):
    row = {
        "Header": {"ColData": [{"value": label}]},
        "Summary": {"ColData": [
            {"value": f"Total for {label}"}, {"value": ""}, {"value": ""}, {"value": ""},
            {"value": ""}, {"value": ""}, {"value": str(balance)}, {"value": ""},
        ]},
    }
    if nested_rows:
        row["Rows"] = {"Row": nested_rows}
    return row


def test_walk_gl_balance_rows_sums_matching_account_sections():
    rows = [
        _gl_summary_row("11100 Chase Operating Bank Account - 9889", 10414.78),
        _gl_summary_row("11110 Highbeam Checking Account - 9625", 8906.56),
        _gl_summary_row("11170 Petty Cash", 0.0),
    ]
    balances: list = []
    _walk_gl_balance_rows(rows, ("11100 Chase Operating Bank Account", "11110 Highbeam Checking"), balances)
    assert balances == [10414.78, 8906.56]  # Petty Cash correctly excluded


def test_walk_gl_balance_rows_recurses_into_nested_group_sections():
    rows = [
        {
            "Header": {"ColData": [{"value": "11000 Cash & Bank"}]},
            "Rows": {"Row": [_gl_summary_row("11100 Chase Operating Bank Account - 9889", 10414.78)]},
            "Summary": {"ColData": [{"value": "Total for 11000 Cash & Bank"}] + [{"value": ""}] * 5 + [{"value": "10414.78"}, {"value": ""}]},
        },
    ]
    balances: list = []
    # Only the leaf account prefix matches - the parent group's own Summary is also a candidate
    # match but has a different label, so this confirms recursion reaches the nested row too.
    _walk_gl_balance_rows(rows, ("11100 Chase Operating Bank Account",), balances)
    assert balances == [10414.78]


def test_fetch_gl_account_balance_sums_via_mocked_get(monkeypatch):
    def fake_get(realm_id, access_token, environment, path, params):
        assert path == "reports/GeneralLedger"
        assert params["end_date"] == "2026-08-18"
        return {"Rows": {"Row": [
            _gl_summary_row("11100 Chase Operating Bank Account - 9889", 10414.78),
            _gl_summary_row("11110 Highbeam Checking Account - 9625", 8906.56),
            _gl_summary_row("11120 Highbeam High Yield Savings Account - 9626", 47707.92),
        ]}}

    monkeypatch.setattr(qbo_client, "_get", fake_get)
    from datetime import date
    result = qbo_client.fetch_gl_account_balance(
        "realm", "token",
        ["11100 Chase Operating Bank Account", "11110 Highbeam Checking", "11120 Highbeam High Yield Savings"],
        date(2026, 8, 18),
    )
    assert round(result, 2) == 67029.26


def _gl_detail_section(label, lines):
    """lines: [(date, amount, running_balance), ...]"""
    return {
        "Header": {"ColData": [{"value": label}]},
        "Rows": {"Row": [
            {"ColData": [
                {"value": d}, {"value": "Expense"}, {"value": ""}, {"value": "Vendor"},
                {"value": "memo"}, {"value": "some account"}, {"value": str(amt)}, {"value": str(bal)},
            ]}
            for d, amt, bal in lines
        ]},
    }


def test_walk_gl_detail_rows_extracts_transaction_lines():
    rows = [_gl_detail_section("71100 Founder/Officer Compensation", [
        ("2026-07-30", 5391.65, 47287.70),
        ("2026-08-13", 5391.65, 52679.35),
    ])]
    sink: list = []
    _walk_gl_detail_rows(rows, "71100 Founder/Officer Compensation", sink)
    assert sink == [
        {"date": "2026-07-30", "amount": 5391.65},
        {"date": "2026-08-13", "amount": 5391.65},
    ]


def test_walk_gl_detail_rows_ignores_other_accounts():
    rows = [
        _gl_detail_section("71100 Founder/Officer Compensation", [("2026-08-13", 5391.65, 52679.35)]),
        _gl_detail_section("55100 Shopify Transaction Fees", [("2026-08-13", -12.34, 100.0)]),
    ]
    sink: list = []
    _walk_gl_detail_rows(rows, "71100 Founder/Officer Compensation", sink)
    assert len(sink) == 1
    assert sink[0]["date"] == "2026-08-13"


def test_fetch_gl_account_transactions_via_mocked_get(monkeypatch):
    def fake_get(realm_id, access_token, environment, path, params):
        assert path == "reports/GeneralLedger"
        return {"Rows": {"Row": [_gl_detail_section("71100 Founder/Officer Compensation", [
            ("2026-07-30", 5391.65, 47287.70),
            ("2026-08-13", 5391.65, 52679.35),
        ])]}}

    monkeypatch.setattr(qbo_client, "_get", fake_get)
    from datetime import date
    result = qbo_client.fetch_gl_account_transactions(
        "realm", "token", "71100 Founder/Officer Compensation",
        date(2026, 5, 1), date(2026, 8, 18),
    )
    assert result == [
        {"date": "2026-07-30", "amount": 5391.65},
        {"date": "2026-08-13", "amount": 5391.65},
    ]


def test_walk_gl_detail_rows_multi_collects_all_matching_accounts():
    """Unlike _walk_gl_detail_rows (single account, early-returns on first match),
    _walk_gl_detail_rows_multi must keep collecting across every account that matches any of
    the given prefixes - this is what lets one GeneralLedger pull replace several
    single-account fetch_gl_account_balance calls."""
    rows = [
        _gl_detail_section("11100 Chase Operating Bank Account", [("2026-08-01", 1000.0, 1000.0)]),
        _gl_detail_section("11110 Highbeam Checking", [("2026-08-02", 500.0, 500.0)]),
        _gl_detail_section("55100 Shopify Transaction Fees", [("2026-08-03", -12.34, 100.0)]),
    ]
    sink: list = []
    _walk_gl_detail_rows_multi(rows, ("11100 Chase Operating Bank Account", "11110 Highbeam Checking"), sink)
    assert sink == [
        {"date": "2026-08-01", "amount": 1000.0},
        {"date": "2026-08-02", "amount": 500.0},
    ]


def test_walk_gl_detail_rows_multi_recurses_into_nested_group_sections():
    nested = [_gl_detail_section("11120 Highbeam High Yield Savings", [("2026-08-05", 250.0, 250.0)])]
    rows = [{"Header": {"ColData": [{"value": "Bank Accounts"}]}, "Rows": {"Row": nested}}]
    sink: list = []
    _walk_gl_detail_rows_multi(rows, ("11120 Highbeam High Yield Savings",), sink)
    assert sink == [{"date": "2026-08-05", "amount": 250.0}]


def test_fetch_gl_account_transactions_multi_via_mocked_get(monkeypatch):
    def fake_get(realm_id, access_token, environment, path, params):
        assert path == "reports/GeneralLedger"
        return {"Rows": {"Row": [
            _gl_detail_section("11100 Chase Operating Bank Account", [("2026-08-01", 1000.0, 1000.0)]),
            _gl_detail_section("11110 Highbeam Checking", [("2026-08-02", 500.0, 500.0)]),
        ]}}

    monkeypatch.setattr(qbo_client, "_get", fake_get)
    from datetime import date
    result = qbo_client.fetch_gl_account_transactions_multi(
        "realm", "token",
        ["11100 Chase Operating Bank Account", "11110 Highbeam Checking"],
        date(2020, 1, 1), date(2026, 8, 18),
    )
    assert result == [
        {"date": "2026-08-01", "amount": 1000.0},
        {"date": "2026-08-02", "amount": 500.0},
    ]
