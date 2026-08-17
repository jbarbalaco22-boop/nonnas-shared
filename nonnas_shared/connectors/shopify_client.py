"""Raw data pulls from Shopify. No business-metric computation here."""
from datetime import date

import requests

from nonnas_shared.config import require_env

API_VERSION = "2025-04"


def get_access_token(myshopify_domain: str) -> str:
    """Client credentials grant. Token is valid 24h — call this once per run, don't cache across runs."""
    client_id = require_env("SHOPIFY_CLIENT_ID")
    client_secret = require_env("SHOPIFY_CLIENT_SECRET")
    response = requests.post(
        f"https://{myshopify_domain}/admin/oauth/access_token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _graphql(myshopify_domain: str, access_token: str, query: str, variables: dict | None = None) -> dict:
    response = requests.post(
        f"https://{myshopify_domain}/admin/api/{API_VERSION}/graphql.json",
        headers={
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json",
        },
        json={"query": query, "variables": variables or {}},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if "errors" in payload:
        raise RuntimeError(f"Shopify GraphQL error: {payload['errors']}")
    return payload["data"]


def get_fulfillment_location_id(myshopify_domain: str, access_token: str, configured_location_gid: str | None) -> str:
    """Return configured_location_gid if set, else auto-discover the single fulfillment location via API."""
    if configured_location_gid:
        return configured_location_gid

    query = """
    query {
      locations(first: 5, query: "fulfills_online_orders:true") {
        nodes { id name }
      }
    }
    """
    data = _graphql(myshopify_domain, access_token, query)
    locations = data["locations"]["nodes"]
    if len(locations) != 1:
        raise RuntimeError(
            f"Expected exactly one fulfillment location, found {len(locations)}: {locations}. "
            "Set shopify.location_gid explicitly in your app config."
        )
    return locations[0]["id"]


_ORDERS_QUERY = """
query($cursor: String, $searchQuery: String!) {
  orders(first: 100, after: $cursor, query: $searchQuery, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      name
      createdAt
      sourceName
      subtotalPriceSet { shopMoney { amount } }
      totalShippingPriceSet { shopMoney { amount } }
      totalDiscountsSet { shopMoney { amount } }
      totalRefundedSet { shopMoney { amount } }
      lineItems(first: 50) {
        nodes { sku quantity }
      }
    }
  }
}
"""


def fetch_orders(myshopify_domain: str, access_token: str, start_date: date, end_date: date) -> list[dict]:
    """Pull orders in [start_date, end_date) via Shopify GraphQL, paginated.

    Channel split: use shopify_channels.classify_source(order["sourceName"]) — a real 4-way
    split (DTC/TikTok/Amazon/Wholesale), not the binary DTC-vs-TikTok split this docstring used
    to describe. Amazon and Wholesale orders that land here are known-incomplete mirrors, not a
    full picture of those channels — see nonnas-finance-audit/CLAUDE.md.
    """
    search_query = f"created_at:>='{start_date.isoformat()}' AND created_at:<'{end_date.isoformat()}'"
    orders: list[dict] = []
    cursor = None
    while True:
        data = _graphql(
            myshopify_domain,
            access_token,
            _ORDERS_QUERY,
            {"cursor": cursor, "searchQuery": search_query},
        )
        page = data["orders"]
        orders.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return orders


def order_revenue_breakdown(order: dict) -> dict:
    """Returns {gross, discounts, refunds, net, shipping} for one order.

    net = subtotal (already after discounts) + shipping - refunds, tax excluded — matches QB's
    net sales figures, which also exclude sales tax. gross = subtotal + discounts, i.e. the
    pre-discount line-item total. Do NOT use Shopify's originalTotalPriceSet for "gross": it
    already has the discount netted out (verified empirically against 300 live orders) and
    includes tax, so subtracting totalDiscountsSet from it double-counts the discount.
    """
    subtotal = float(order["subtotalPriceSet"]["shopMoney"]["amount"])
    shipping = float(order["totalShippingPriceSet"]["shopMoney"]["amount"])
    discounts = float(order["totalDiscountsSet"]["shopMoney"]["amount"])
    refunds = float(order["totalRefundedSet"]["shopMoney"]["amount"])
    return {
        "gross": subtotal + discounts,
        "discounts": discounts,
        "shipping": shipping,
        "refunds": refunds,
        "net": subtotal + shipping - refunds,
    }
