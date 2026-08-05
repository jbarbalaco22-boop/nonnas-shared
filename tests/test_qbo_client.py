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
