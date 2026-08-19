"""Regression coverage for order_revenue_breakdown — the calc that used to be duplicated
(and buggy) in nonnas-finance-audit's audit.py: subtotalPriceSet is already post-discount
and pre-tax, so gross/net must not double-subtract the discount or include tax."""
from nonnas_shared.connectors.shopify_client import is_first_order, order_revenue_breakdown


def _order(subtotal, shipping, discounts, refunds):
    return {
        "subtotalPriceSet": {"shopMoney": {"amount": str(subtotal)}},
        "totalShippingPriceSet": {"shopMoney": {"amount": str(shipping)}},
        "totalDiscountsSet": {"shopMoney": {"amount": str(discounts)}},
        "totalRefundedSet": {"shopMoney": {"amount": str(refunds)}},
    }


def test_net_is_subtotal_plus_shipping_minus_refunds():
    result = order_revenue_breakdown(_order(90.00, 8.00, 10.00, 5.00))
    assert result["net"] == 93.0


def test_gross_adds_discount_back_onto_already_net_subtotal():
    result = order_revenue_breakdown(_order(24.30, 0.0, 8.70, 0.0))
    assert result["gross"] == 33.0


def test_no_discount_no_refund():
    result = order_revenue_breakdown(_order(71.28, 0.0, 0.0, 0.0))
    assert result["net"] == 71.28
    assert result["gross"] == 71.28


def test_full_refund_zeroes_out_net():
    result = order_revenue_breakdown(_order(24.30, 0.0, 8.70, 24.30))
    assert result["net"] == 0.0


def test_is_first_order_true_when_this_order_is_the_customers_earliest():
    order = {"id": "gid://shopify/Order/1", "customer": {"id": "gid://shopify/Customer/1",
              "orders": {"nodes": [{"id": "gid://shopify/Order/1"}]}}}
    assert is_first_order(order) is True


def test_is_first_order_false_when_an_earlier_order_exists():
    order = {"id": "gid://shopify/Order/2", "customer": {"id": "gid://shopify/Customer/1",
              "orders": {"nodes": [{"id": "gid://shopify/Order/1"}]}}}
    assert is_first_order(order) is False


def test_is_first_order_none_when_no_customer_attached():
    order = {"id": "gid://shopify/Order/3", "customer": None}
    assert is_first_order(order) is None


def test_is_first_order_none_when_customer_has_no_orders_returned():
    order = {"id": "gid://shopify/Order/4", "customer": {"id": "gid://shopify/Customer/1", "orders": {"nodes": []}}}
    assert is_first_order(order) is None
