"""Regression coverage for a real bug: Amazon and Faire/Wholesale orders were
silently lumped into DTC because the old logic only special-cased 'tiktok'."""
from nonnas_shared.connectors.shopify_channels import classify_source


def test_tiktok():
    assert classify_source("tiktok") == "TikTok"


def test_amazon_is_not_dtc():
    assert classify_source("amazon") == "Amazon"


def test_faire_maps_to_wholesale():
    assert classify_source("faire") == "Wholesale"


def test_draft_orders_excluded():
    assert classify_source("shopify_draft_order") is None


def test_web_is_dtc():
    assert classify_source("web") == "DTC"


def test_unknown_numeric_app_id_defaults_to_dtc():
    assert classify_source("2329312") == "DTC"


def test_subscription_sources_are_dtc():
    assert classify_source("subscription_contract") == "DTC"
    assert classify_source("subscription_contract_checkout_one") == "DTC"


def test_empty_or_none_defaults_to_dtc():
    assert classify_source(None) == "DTC"
    assert classify_source("") == "DTC"
