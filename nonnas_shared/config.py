"""Shared config loading — env vars and the packaged JSON/CSV reference data."""
import csv
import json
import os
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent / "data"


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_json_config(filename: str) -> dict:
    with open(CONFIG_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


def load_qbo_account_map() -> dict:
    return load_json_config("qbo_account_map.json")


def load_sku_map() -> dict:
    """Returns {sku: {"name": str, "status": "active"|"upcoming", "note": str|absent}} — the
    canonical SKU registry (Nonna's Olive Oil's product lineup), so every SKU-level report/tool
    uses the same keys and display names instead of each hand-deriving them from raw QBO Item
    names or Shopify SKU strings. Update this file, not a hand-copied version elsewhere, when
    a SKU launches, retires, or gets renamed."""
    return load_json_config("sku_map.json")["skus"]


def load_business_context() -> str:
    """The institutional knowledge needed to interpret Nonna's financial data correctly
    (channel attribution gaps, COGS correction status, etc.) — see business_context.md's own
    header for why this lives here rather than being duplicated by hand in a system prompt."""
    with open(CONFIG_DIR / "business_context.md", encoding="utf-8") as f:
        return f.read()


def load_channel_units_by_month() -> dict:
    """Returns {month: {"DTC": int|None, "TikTok": int|None, "Amazon": int|None,
    "Wholesale": int|None, "total_units": int|None, "note": str}} — the canonical,
    hand-reconciled units-sold-by-channel reference, used both to build the historical COGS
    corrections and as a fallback reference for the chat assistant (live Shopify data doesn't
    reliably cover Amazon/Wholesale, and doesn't exist at all before the channel split started
    in 2025-04 — see the note field on those early months).

    Channel values are None for months before the per-channel split was tracked; only
    total_units is populated for those. Stops at the first blank row — the source file has an
    unrelated wholesale-by-vendor table appended below the main monthly one.
    """
    result = {}
    with open(CONFIG_DIR / "channel_units_by_month.csv", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)  # header row
        for row in reader:
            if not row or not row[0]:
                break
            row = (row + [""] * 7)[:7]
            month, dtc, tiktok, amazon, wholesale, total, note = row
            result[month] = {
                "DTC": int(dtc) if dtc else None,
                "TikTok": int(tiktok) if tiktok else None,
                "Amazon": int(amazon) if amazon else None,
                "Wholesale": int(wholesale) if wholesale else None,
                "total_units": int(total) if total else None,
                "note": note,
            }
    return result
