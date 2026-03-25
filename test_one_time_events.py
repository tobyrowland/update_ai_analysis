#!/usr/bin/env python3
"""Quick test: inspect EODHD one-time event fields for a selection of tickers.

Usage:
    EODHD_API_KEY=xxx python test_one_time_events.py
"""
import os, sys, json, requests

API_KEY = os.environ.get("EODHD_API_KEY")
if not API_KEY:
    print("Set EODHD_API_KEY env var"); sys.exit(1)

# Mix of companies likely to have one-time items
TICKERS = [
    ("NVDA", "US"),   # large cap, clean
    ("INTC", "US"),   # restructuring charges
    ("META", "US"),   # had layoff charges
    ("BABA", "US"),   # regulatory fines / asset disposals
    ("SMCI", "US"),   # accounting issues
    ("PLTR", "US"),   # SBC heavy
    ("RBLX", "US"),   # pre-profit
]

ONE_TIME_FIELDS = [
    "nonRecurring",
    "extraordinaryItems",
    "discontinuedOperations",
    "otherItems",
    "otherNonCashItems",
    "otherNonOperatingIncome",
    "totalOtherIncomeExpenseNet",
    "nonOperatingIncomeNetOther",
    "minorityInterest",
    "effectOfAccountingCharges",
    "taxProvision",
    "incomeTaxExpense",
    "incomeBeforeTax",
    "netIncomeFromContinuingOps",
    "netIncome",
    "operatingIncome",
]

def safe_float(v):
    if v is None or v == "" or v == "None":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None

def analyze_ticker(ticker, exchange):
    url = f"https://eodhd.com/api/fundamentals/{ticker}.{exchange}"
    resp = requests.get(url, params={"api_token": API_KEY, "fmt": "json"}, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    inc_q = data.get("Financials", {}).get("Income_Statement", {}).get("quarterly", {})
    dates = sorted(inc_q.keys(), reverse=True)

    print(f"\n{'='*70}")
    print(f"  {ticker}.{exchange}")
    print(f"{'='*70}")

    if not dates:
        print("  No quarterly income data found")
        return

    # Show last 4 quarters of one-time fields
    for date in dates[:4]:
        entry = inc_q[date]
        rev = safe_float(entry.get("totalRevenue"))
        oi = safe_float(entry.get("operatingIncome"))
        ni = safe_float(entry.get("netIncome"))
        nr = safe_float(entry.get("nonRecurring"))
        ei = safe_float(entry.get("extraordinaryItems"))
        dc = safe_float(entry.get("discontinuedOperations"))
        oth = safe_float(entry.get("otherItems"))
        onc = safe_float(entry.get("otherNonCashItems"))
        toi = safe_float(entry.get("totalOtherIncomeExpenseNet"))

        print(f"\n  --- {date} ---")
        print(f"    Revenue:           {rev:>15,.0f}" if rev else "    Revenue:           N/A")
        print(f"    Operating Income:  {oi:>15,.0f}" if oi else "    Operating Income:  N/A")
        print(f"    Net Income:        {ni:>15,.0f}" if ni else "    Net Income:        N/A")
        print(f"    nonRecurring:      {nr:>15,.0f}" if nr else "    nonRecurring:      -")
        print(f"    extraordinaryItems:{ei:>15,.0f}" if ei else "    extraordinaryItems: -")
        print(f"    discontinuedOps:   {dc:>15,.0f}" if dc else "    discontinuedOps:   -")
        print(f"    otherItems:        {oth:>15,.0f}" if oth else "    otherItems:        -")
        print(f"    otherNonCashItems: {onc:>15,.0f}" if onc else "    otherNonCashItems: -")
        print(f"    totalOtherIncExp:  {toi:>15,.0f}" if toi else "    totalOtherIncExp:  -")

        # Flag: large gap between operating income and net income
        if oi and ni and rev and rev > 0:
            gap_pct = abs(ni - oi) / rev * 100
            if gap_pct > 10:
                print(f"    ⚠ OI→NI gap = {gap_pct:.1f}% of revenue (potential one-time items)")

        # Flag: nonRecurring is material (>5% of revenue)
        if nr and rev and rev > 0 and abs(nr) / rev > 0.05:
            print(f"    ⚠ nonRecurring = {abs(nr)/rev*100:.1f}% of revenue")

    # Also dump ALL fields from latest quarter for reference
    print(f"\n  --- All fields in latest quarter ({dates[0]}) ---")
    for k, v in sorted(inc_q[dates[0]].items()):
        fv = safe_float(v)
        if fv and fv != 0:
            print(f"    {k}: {fv:>15,.0f}")

for ticker, exchange in TICKERS:
    try:
        analyze_ticker(ticker, exchange)
    except Exception as e:
        print(f"\n  {ticker}: ERROR - {e}")
