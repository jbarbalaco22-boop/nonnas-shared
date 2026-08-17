"""Tests for load_channel_units_by_month and load_business_context — both read the real
packaged files, not a fixture, since the whole point is confirming they actually ship with the
installed package and load correctly from wherever it's installed."""
from nonnas_shared.config import load_business_context, load_channel_units_by_month


def test_loads_real_packaged_file():
    result = load_channel_units_by_month()
    assert len(result) > 0


def test_pre_split_months_have_total_but_no_channel_breakdown():
    result = load_channel_units_by_month()
    row = result["2024-09"]
    assert row["DTC"] is None
    assert row["TikTok"] is None
    assert row["total_units"] == 687
    assert "already posted" in row["note"]


def test_post_split_months_have_full_channel_breakdown():
    result = load_channel_units_by_month()
    row = result["2026-07"]
    assert row["DTC"] == 234
    assert row["TikTok"] == 220
    assert row["Amazon"] == 390
    assert row["Wholesale"] == 12


def test_stops_before_the_unrelated_vendor_detail_table():
    result = load_channel_units_by_month()
    assert "Customer" not in result
    assert "(unnamed)" not in result


def test_business_context_loads_and_mentions_known_gaps():
    context = load_business_context()
    assert len(context) > 0
    assert "Wholesale" in context
    assert "A2X" in context
