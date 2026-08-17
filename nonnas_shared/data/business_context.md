# Business context for interpreting Nonna's Italian Goods financial data

This is the single source of the institutional knowledge needed to interpret revenue, COGS,
and unit data correctly for Nonna's Italian Goods (Nonna's Olive Oil) — not just report raw
numbers. Loaded directly into the chat assistant's system prompt at runtime; also referenced
from `nonnas-shared/CLAUDE.md` for anyone working on this codebase, so this stays the one place
to update rather than two hand-maintained copies drifting apart.

(This file intentionally excludes QBO API implementation details — unreliable report endpoints,
Class ID numbers, CSV import formatting — that matter for *writing code against* QBO, not for
*interpreting* the numbers it returns. See `nonnas-shared/CLAUDE.md` for those.)

## Channel attribution gaps

- TikTok and Amazon revenue post to QuickBooks via A2X as settlement-period summary journal
  entries, not raw order-by-order data. A gap between QuickBooks and Shopify for these two
  channels may reflect settlement timing, not a real error.
- A2X only has full payout detail for the Shopify Payments gateway. Any other checkout path
  (PayPal, Amazon Pay, Faire, manual/wholesale orders) gets bucketed as an "Other Payment
  Gateway" and defaults to **Class=DTC** regardless of the real source. This has been found and
  cleaned up three separate times historically — treat any DTC-tagged line with a non-Shopify
  description, or an "A2XUS-" DocNumber prefix (denotes the native Amazon connection), as
  suspect rather than taking the DTC tag at face value.
- Amazon's presence in Shopify is a known-incomplete, sometimes-duplicated mirror of real
  Amazon sales — there is no direct Amazon Seller Central connection yet. Never state Amazon
  unit/order counts sourced from Shopify as complete.
- Wholesale revenue is typically recorded as a bank Deposit with no Item or Quantity attached,
  so Wholesale COGS and units are frequently $0/zero even when real sales happened — that's a
  known process gap, not evidence nothing sold.
- Ad spend accounts (Meta/Google/TikTok/Amazon Ads, Marketplace Advertising) are not tagged by
  Class in QuickBooks — channel attribution for ads comes from mapping each platform to its
  channel by account, not from a QuickBooks Class field. The business has confirmed Marketplace
  Advertising is DTC spend, so it's included in DTC's ad spend and ROAS. Paid Collaborations and
  Affiliate Commissions aren't confidently attributable to a single channel and are excluded from
  per-channel figures rather than guessed at (still included in company-wide ad totals).
- Email & SMS Marketing tooling is real DTC-attributed cost — it reduces DTC's contribution
  margin (as `other_marketing`) but is kept out of DTC's `ads` figure on purpose, since folding it
  in would distort ROAS (net_sales / ads), which is meant to measure working paid-media
  efficiency, not marketing/retention tooling spend.
- Creative Agency Fees (~$25.6K YTD as of 2026-08-17) and Attribution Tools (~$6.5K) are real
  marketing-adjacent costs the business has chosen to leave out of channel-level contribution
  margin for now, rather than guess at an allocation — don't imply they're already reflected in
  any channel's numbers. Same for Sales Brokers / Wholesale (~$15K) — a cost that looks
  Wholesale-specific by name, but is deliberately excluded from Wholesale's contribution margin
  today; if asked about Wholesale's true profitability, mention this cost sits outside the
  reported figure.

## COGS correction status

Historical COGS was corrected against a physical inventory count (2,267 units as of 2026-08-03)
using a unified formula: `(true_cost_per_unit × units_sold) − actual_deducted`, applied per
channel per month through **July 2026**. August 2026 onward reflects the raw, uncorrected
automated deduction mechanism (a static per-unit rate that doesn't track true landed cost) —
treat August-forward channel margins as meaningfully less reliable than prior months until a
similar correction has been run for that period.

## Historical unit reference

`get_unit_reference` (a tool available to this assistant) returns a hand-reconciled units-sold-
by-channel table covering every month back to September 2024 — built by cross-referencing
Amazon's and Faire's own transaction reports, not just Shopify's incomplete mirror of them.
Prefer it over live Shopify data for:
- Any month before 2025-04, where no live per-channel split exists at all.
- A sanity check when live Amazon/Wholesale unit counts look wrong (per the gaps above).

It is a fixed historical snapshot, not live — it won't reflect anything more recent than
whenever it was last updated.
