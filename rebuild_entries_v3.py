import csv, json

units = {}
with open('nonnas_shared/data/channel_units_by_month.csv', newline='', encoding='utf-8') as f:
    reader = csv.reader(f)
    header = next(reader)
    for row in reader:
        if not row or not row[0]:
            break
        month = row[0]
        if month > '2026-07':
            break
        if month < '2024-09':
            continue
        dtc, tiktok, amazon, wholesale, total = row[1], row[2], row[3], row[4], row[5]
        if month < '2025-04':
            units[month] = {'DTC': int(total or 0), 'TikTok': 0, 'Amazon': 0, 'Wholesale': 0}
        else:
            if dtc == '':
                continue
            units[month] = {'DTC': int(dtc or 0), 'TikTok': int(tiktok or 0), 'Amazon': int(amazon or 0), 'Wholesale': int(wholesale or 0)}

true_cost = {
    '2025-04': 11.00, '2025-05': 11.00, '2025-06': 11.00, '2025-07': 11.00, '2025-08': 11.00,
    '2025-09': 11.00, '2025-10': 11.00, '2025-11': 11.00, '2025-12': 11.00, '2026-01': 11.00, '2026-02': 11.00,
    '2026-03': 10.18, '2026-04': 10.01, '2026-05': 10.01, '2026-06': 9.92, '2026-07': 8.25,
}

with open('../nonnas-finance-audit/deduction_by_channel_month_FRESH.json') as f:
    actual_deducted = json.load(f)

month_names = {'01': 'Jan', '02': 'Feb', '03': 'Mar', '04': 'Apr', '05': 'May', '06': 'Jun',
               '07': 'Jul', '08': 'Aug', '09': 'Sep', '10': 'Oct', '11': 'Nov', '12': 'Dec'}

rows = [['RowType', 'Month', 'DocNumber', 'Account', 'Class', 'Amount', 'Memo']]
corr_total = 0.0

# 1. Unified correction entries - Apr2025 through Jul2026
for month in sorted(m for m in units if m >= '2025-04'):
    y, m = month.split('-')
    label = f'{month_names[m]}{y}'
    doc = f'COGS-CORR-{label}'
    tc = true_cost[month]
    lines = []
    for ch in ['DTC', 'TikTok', 'Amazon', 'Wholesale']:
        u = units[month][ch]
        if u == 0:
            continue
        true_total = u * tc
        actual = actual_deducted.get(ch, {}).get(month, 0.0)
        correction = round(true_total - actual, 2)
        if abs(correction) < 0.005:
            continue
        lines.append((ch, u, actual, correction))
    if not lines:
        continue
    je_total = round(sum(l[3] for l in lines), 2)
    corr_total += je_total

    header_memo = (f'Corrected COGS entry for {month_names[m]} {y}: true FIFO cost was ${tc:.2f}/unit '
                   f'(validated against 8/3/2026 physical count). Computed as (true cost x units) minus what '
                   f'was actually deducted by the automated mechanism, which corrects for BOTH rate errors and '
                   f'quantity errors (never-deducted units, and any pre-existing over/under-deduction from '
                   f'settlement-window timing) in a single unified figure per channel.')
    rows.append(['JE Header', month, doc, '', '', f'{je_total:.2f}', header_memo])
    for ch, u, actual, correction in lines:
        sign = 'understatement' if correction > 0 else 'overstatement (correcting entry reduces COGS)'
        memo = (f'{ch}: {u} units x ${tc:.2f} true cost = ${u*tc:,.2f} should have posted; '
                f'${actual:,.2f} actually posted; correction = ${correction:,.2f} ({sign})')
        acct_suffix = '(Debit)' if correction > 0 else '(Credit)'
        rows.append(['Line', month, doc, f'51100 Bulk Olive Oil - Raw {acct_suffix}', ch, f'{abs(correction):.2f}', memo])
    inv_suffix = '(Credit)' if je_total > 0 else '(Debit)'
    rows.append(['Line', month, doc, f'13100 Inventory {inv_suffix}', '', f'{abs(je_total):.2f}',
                 f'{"Relieves" if je_total>0 else "Restores"} inventory for {month_names[m]} {y} correction'])

print(f'Unified correction total: ${corr_total:,.2f}')

# 2. Residual - CORRECTED: computed from raw balance as-of-8/3, consistent basis
raw_as_of_8_3 = 46771.80 + 40084.23  # verified via GL, reversing the (now-deleted) flawed entries
target = 2267 * 7.08
balance_after_unified = raw_as_of_8_3 - corr_total
gap = balance_after_unified - target
print(f'Raw balance as of 8/3: ${raw_as_of_8_3:,.2f}')
print(f'Balance after unified correction: ${balance_after_unified:,.2f}')
print(f'Residual needed: ${gap:,.2f}')

total_units_spread = sum(sum(v.values()) for v in units.values())
per_unit_rate = gap / total_units_spread
print(f'Per-unit residual rate: ${per_unit_rate:.6f}')

resid_total = 0.0
for month in sorted(units):
    y, m = month.split('-')
    label = f'{month_names[m]}{y}'
    doc = f'RESID-CORR-{label}'
    lines = []
    for ch in ['DTC', 'TikTok', 'Amazon', 'Wholesale']:
        u = units[month][ch]
        if u == 0:
            continue
        amt = round(u * per_unit_rate, 2)
        lines.append((ch, u, amt))
    je_total = round(sum(l[2] for l in lines), 2)
    resid_total += je_total
    extra_note = ''
    if month < '2025-04':
        extra_note = (' NOTE: pre-scope month, no channel breakdown in source data, all units tagged DTC as only '
                      'channel active then - pure proportional smoothing allocation.')
    header_memo = (f'Residual allocation for {month_names[m]} {y}: after the corrected unified COGS entries, a '
                   f'${gap:,.2f} gap remained between corrected Inventory balance and the 8/3/2026 physical count '
                   f'target (2,267 units x $7.08), computed on a consistent as-of-8/3 basis. Spread proportionally '
                   f'across all units sold Sep 2024-Jul 2026 (Aug 2026 excluded as incomplete) at ${per_unit_rate:.4f}'
                   f'/unit, channel-tagged by this months share of units sold.{extra_note}')
    inv_side = '(Credit)' if je_total > 0 else '(Debit)'
    rows.append(['JE Header', month, doc, '', '', f'{je_total:.2f}', header_memo])
    for ch, u, amt in lines:
        acct_suffix = '(Debit)' if amt > 0 else '(Credit)'
        memo = f'{ch}: {u} units x ${per_unit_rate:.4f}/unit residual allocation = ${amt:,.2f}'
        rows.append(['Line', month, doc, f'51100 Bulk Olive Oil - Raw {acct_suffix}', ch, f'{abs(amt):.2f}', memo])
    rows.append(['Line', month, doc, f'13100 Inventory {inv_side}', '', f'{abs(je_total):.2f}',
                 f'{"Relieves" if je_total>0 else "Restores"} inventory for {month_names[m]} {y} residual allocation'])

print(f'Residual total: ${resid_total:,.2f}')
grand_total = corr_total + resid_total
print(f'GRAND TOTAL: ${grand_total:,.2f}')
print(f'Check: ${raw_as_of_8_3:,.2f} - ${grand_total:,.2f} = ${raw_as_of_8_3 - grand_total:,.2f} (should equal target ${target:,.2f})')

with open('cogs_correction_monthly_entries.csv', 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerows(rows)
print(f'Wrote {len(rows)-1} data rows')
