"""Tests for fetch_account_balances' name-matching logic — regression coverage
for a real bug where an exact WHERE Name IN (...) match never matched
anything, because targets carry a leading account-number prefix that QBO's
Account.Name field doesn't have, and real names often carry their own
trailing suffix (an account mask) that the target doesn't have either."""
from nonnas_shared.connectors.qbo_client import _LEADING_ACCOUNT_NUMBER


def test_strips_leading_account_number():
    assert _LEADING_ACCOUNT_NUMBER.sub("", "11100 Chase Operating Bank Account") == "Chase Operating Bank Account"


def test_leaves_unprefixed_name_untouched():
    assert _LEADING_ACCOUNT_NUMBER.sub("", "Shopify Clearing") == "Shopify Clearing"


def test_stripped_target_is_a_prefix_of_the_real_suffixed_name():
    """Real QBO names often carry an account mask suffix the map doesn't have,
    e.g. real 'Chase Operating Bank Account - 9889' vs stripped target
    'Chase Operating Bank Account' -> must match via startswith, not equality."""
    stripped = _LEADING_ACCOUNT_NUMBER.sub("", "11100 Chase Operating Bank Account")
    real_name = "Chase Operating Bank Account - 9889"
    assert real_name.startswith(stripped)
