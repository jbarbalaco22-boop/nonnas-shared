# nonnas-shared

Shared package used by `nonnas-finance-audit` and `nonnas-daily-operator` for QBO and Shopify access on the Nonna's Italian Goods / Nonna's Olive Oil bookkeeping engagement. Company entity name in QBO is "Nonna's Italian Goods."

## Structure
- `nonnas_shared/connectors/qbo_client.py`, `qbo_auth.py` — QBO API client + OAuth token handling
- `nonnas_shared/connectors/shopify_client.py`, `shopify_channels.py` — Shopify client + DTC/TikTok/Amazon/Wholesale channel classification
- `nonnas_shared/connectors/channel_financials.py` — per-channel financial rollups
- `nonnas_shared/data/qbo_account_map.json` — source of truth for which QBO accounts belong to which channel/bucket (net sales, COGS, ads, fees, clearing accounts). Read this file before writing any new channel-allocation logic rather than re-deriving account names by hand.
- `tests/` — covers `qbo_client`, account matching, `shopify_channels`
- `rebuild_entries_v2.py`, `rebuild_entries_v3.py` — one-off scripts that built the COGS correction journal entries (see below). `v3` is the current/authoritative version.

## QBO API gotchas (all discovered the hard way — trust these over intuition)

- **`BalanceSheet` report `end_date` is unreliable.** Passing a historical `end_date` has silently returned today's balance regardless of the date passed. **Never use it for a date-scoped balance check.** Instead use the `GeneralLedger` report with explicit `start_date`/`end_date` and manually sum the `debt_amt`/`credit_amt` columns from the raw rows. This method has been cross-validated multiple times and is trusted.
- **`Account.CurrentBalance` via direct GET is unreliable** for the Inventory account — has returned stale/nonsensical values multiple times while the GL-summing method gave consistent, correct answers. Never trust it; always verify via GL summation or (carefully, date-scoped) Balance Sheet.
- **`InventoryAdjustment` with only `NewQty`** (no cost fields) triggers QBO's own automatic average-cost dollar calculation — this is NOT controllable via the API. Passing `UnitCost` or `Value` as explicit fields on `ItemAdjustmentLineDetail` both fail QBO API validation (error code 2010, "invalid or unsupported property"). There is no known way to force a guaranteed-zero-dollar-impact quantity-only adjustment via this API on this QBO account. This caused two real, unwanted ~$45,735.93 P&L hits before we gave up on it — **do not attempt QtyOnHand adjustments via InventoryAdjustment; the user has decided not to reference QBO's QtyOnHand at all.**
- **QBO Class internal IDs** (query `SELECT * FROM Class` to reconfirm if ever in doubt — do not guess, a wrong guess here silently mis-tags live journal entries): DTC=1000000004, TikTok=1000000001, Amazon=1000000003, Wholesale=1000000005.
- **CSV bulk journal-entry import format**: headers must be exactly `Journal No,Journal Date,Account Name,Debits,Credits,Description,Name,Currency,Location,Class` — no literal asterisks (QBO's sample template uses asterisks only as footnote markers). Write with `encoding='utf-8-sig'` (BOM) — plain `utf-8` causes QBO to misread special characters (e.g. the en-dash in account names) as mojibake. Real account name is "51100 Bulk Olive Oil **–** Raw" (en-dash, U+2013, not a hyphen).

## A2X / Shopify structural gap (recurring — watch for this every time)

A2X only has full payout detail for the Shopify Payments gateway. Any other checkout path — PayPal, Amazon Pay, Faire, manual/wholesale orders, and (for a period) TikTok — gets bucketed as an "Other Payment Gateway." A2X can still recognize *that a sale happened* (crediting revenue), defaulting the **Class to DTC** regardless of true source, but has no visibility into that gateway's own settlement, so the cash side often never gets recorded (money sits stuck in Shopify Clearing). This exact pattern has been found and cleaned up **three separate times** this engagement: Wholesale/Faire orders mistagged DTC, Amazon-marketplace orders mirrored into Shopify mistagged DTC, and native Amazon A2X entries mistagged DTC. Treat any DTC-tagged line with a non-Shopify-looking description (or an "A2XUS-" DocNumber prefix, which denotes the native Amazon connection) as suspect. A durable process fix (QBO bank rules + reclass rule) is still outstanding — see `nonnas-finance-audit/CLAUDE.md` open items.

## COGS correction methodology (established, do not regress to the old approach)

The correct formula for a per-channel/month COGS correction is the **unified formula**:

```
correction[channel][month] = (true_cost[month] × units_sold[channel][month]) − actual_deducted[channel][month]
```

This produces a signed result (positive = understated COGS, needs a debit; negative = overstated, needs a credit) in one step. Do **not** use the earlier, flawed two-part approach (`units × (true_cost − flat_rate)` rate-correction plus a separate positive-only "catch-up" entry) — that implicitly assumed the automated deduction was always exactly the flat rate, which is false whenever settlement-window timing caused the automated mechanism to already over- or under-deduct. That flawed version shipped once and had to be fully rebuilt after it silently crushed DTC's gross margin in June/July 2026 (down to ~20-24%) while looking fine in aggregate.

When computing a residual/plug against a physical inventory count, **compute the balance on a basis consistent with the count date** (e.g. "as of 8/3"), never "today's" balance — mixing bases produced a wrong-signed residual once and required a second full rebuild.
