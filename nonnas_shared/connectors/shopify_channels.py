"""Maps a Shopify order's sourceName (aka "Source" in CSV exports) to the
channel buckets QuickBooks' account map uses: DTC, TikTok, Amazon, Wholesale.

Confirmed against a real order export (2026-08-05) by inspecting Note
Attributes/Payment Method/Tags per source value, not guessed:
  - "amazon" carries real Amazon order IDs and delivery dates -> Amazon
  - "faire" carries Faire order numbers and "Wholesale" tags -> Wholesale
  - "tiktok" carries Cedcommerce TikTok Shop tags -> TikTok
  - "shopify_draft_order" -> excluded entirely, not a real completed sale
  - everything else (web, Instagram app id, Appstle subscription sources,
    unlabeled numeric app ids) -> DTC, since they're all direct storefront
    or subscription checkouts, not a separate channel QuickBooks tracks
"""

_CHANNEL_BY_SOURCE = {
    "tiktok": "TikTok",
    "amazon": "Amazon",
    "faire": "Wholesale",
}

_EXCLUDED_SOURCES = {"shopify_draft_order"}


def classify_source(source_name: str | None) -> str | None:
    """Returns 'DTC', 'TikTok', 'Amazon', 'Wholesale', or None if the order
    should be excluded from revenue entirely (e.g. draft orders)."""
    source_name = (source_name or "").strip()
    if source_name in _EXCLUDED_SOURCES:
        return None
    return _CHANNEL_BY_SOURCE.get(source_name, "DTC")
