#!/usr/bin/env python3
"""
EODHD Financial Data Updater for EJ2N Spreadsheet.

Reads tickers from the 'AI Analysis' sheet, fetches fundamental financial data
from the EODHD API, calculates key metrics (including r40_score and
fundamentals_snapshot), and writes results back to the spreadsheet.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dateutil import parser as dateparser
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SPREADSHEET_ID = "1js3dUTJtKhY1dUcwzYUGBOdKDZXBurLtRGgcIV8msYk"
SHEET_NAME = "AI Analysis"
EODHD_BASE_URL = "https://eodhd.com/api/fundamentals"
DELAY_BETWEEN_CALLS = 1  # seconds between EODHD API calls
BATCH_SIZE = 5
STALENESS_DAYS = 7  # re-fetch if data older than this

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ---------------------------------------------------------------------------
# Sheet layout — column groups, display names, widths
# ---------------------------------------------------------------------------

# Each group: (group_name, header_bg_hex, [column_keys...])
GROUPS = [
    ("COMPANY", "1B3A4B", [
        "ticker", "company", "description",
    ]),
    ("REVENUE", "1B3A4B", [
        "annual_revenue_5y", "quarterly_revenue",
        "rev_growth_ttm", "rev_growth_qoq", "rev_cagr_3y", "rev_consistency",
    ]),
    ("MARGINS", "1B3A4B", [
        "gross_margin_ttm", "gross_margin_trend",
        "operating_margin_ttm", "net_margin_ttm", "net_margin_yoy_delta",
        "fcf_margin_ttm",
    ]),
    ("EFFICIENCY", "1B3A4B", [
        "opex_pct_revenue", "sm_rd_pct_revenue",
        "rule_of_40", "qtrs_to_profitability",
    ]),
    ("EARNINGS", "1B3A4B", [
        "eps_quarterly", "eps_yoy_pct",
    ]),
    ("AI NARRATIVE", "2E4057", [
        "r40_score", "fundamentals_snapshot",
        "short_outlook", "outlook", "risks",
        "ai_analysis_date", "fundamentals_date",
    ]),
]

DISPLAY_NAMES = {
    "ticker":               "Ticker",
    "company":              "Company",
    "description":          "Description",
    "annual_revenue_5y":    "Annual Revenue (5Y)",
    "quarterly_revenue":    "Quarterly Revenue",
    "rev_growth_ttm":       "Rev Growth TTM %",
    "rev_growth_qoq":       "Rev Growth QoQ %",
    "rev_cagr_3y":          "Rev CAGR 3Y %",
    "rev_consistency":      "Rev Consistency Score",
    "gross_margin_ttm":     "Gross Margin %",
    "gross_margin_trend":   "GM Trend (Qtly)",
    "operating_margin_ttm": "Operating Margin %",
    "net_margin_ttm":       "Net Margin %",
    "net_margin_yoy_delta": "Net Margin YoY Δ",
    "fcf_margin_ttm":       "FCF Margin %",
    "opex_pct_revenue":     "Opex % of Revenue",
    "sm_rd_pct_revenue":    "S&M+R&D % of Revenue",
    "rule_of_40":           "Rule of 40",
    "qtrs_to_profitability": "Qtrs to Profitability",
    "eps_quarterly":        "EPS Qtrly",
    "eps_yoy_pct":          "EPS YoY %",
    "r40_score":            "R40 Score",
    "fundamentals_snapshot": "Fundamentals Snapshot",
    "short_outlook":        "Short Outlook",
    "outlook":              "Outlook",
    "risks":                "Key Risks",
    "ai_analysis_date":     "AI Analyzed",
    "fundamentals_date":    "Fundamentals Date",
}

COL_WIDTHS = {
    "ticker": 10,
    "company": 25,
    "description": 40,
    "annual_revenue_5y": 45,
    "quarterly_revenue": 22,
    "rev_growth_ttm": 18,
    "rev_growth_qoq": 18,
    "rev_cagr_3y": 18,
    "rev_consistency": 18,
    "gross_margin_ttm": 18,
    "gross_margin_trend": 14,
    "operating_margin_ttm": 18,
    "net_margin_ttm": 18,
    "net_margin_yoy_delta": 18,
    "fcf_margin_ttm": 18,
    "opex_pct_revenue": 18,
    "sm_rd_pct_revenue": 22,
    "rule_of_40": 14,
    "qtrs_to_profitability": 18,
    "eps_quarterly": 14,
    "eps_yoy_pct": 14,
    "r40_score": 22,
    "fundamentals_snapshot": 55,
    "short_outlook": 50,
    "outlook": 80,
    "risks": 50,
    "ai_analysis_date": 16,
    "fundamentals_date": 16,
}

# Columns that should be formatted as percentages
PCT_COLS = {
    "rev_growth_ttm", "rev_growth_qoq", "rev_cagr_3y",
    "gross_margin_ttm", "operating_margin_ttm", "net_margin_ttm",
    "net_margin_yoy_delta", "fcf_margin_ttm",
    "opex_pct_revenue", "sm_rd_pct_revenue", "eps_yoy_pct",
}

# Columns that should be formatted as decimals
DECIMAL_COLS = {"rule_of_40"}

# Columns that should be formatted as dollars
DOLLAR_COLS = {"eps_quarterly"}

# Columns populated by EODHD (as opposed to AI narrative columns)
EODHD_COLUMNS = [
    "annual_revenue_5y", "quarterly_revenue",
    "rev_growth_ttm", "rev_growth_qoq", "rev_cagr_3y", "rev_consistency",
    "gross_margin_ttm", "gross_margin_trend",
    "operating_margin_ttm", "net_margin_ttm", "net_margin_yoy_delta",
    "fcf_margin_ttm",
    "opex_pct_revenue", "sm_rd_pct_revenue",
    "rule_of_40", "qtrs_to_profitability",
    "eps_quarterly", "eps_yoy_pct",
    "r40_score", "fundamentals_snapshot", "fundamentals_date",
]

# ---------------------------------------------------------------------------
# Flat column list derived from GROUPS
# ---------------------------------------------------------------------------

ALL_COLUMNS = []
for _, _, cols in GROUPS:
    ALL_COLUMNS.extend(cols)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging() -> logging.Logger:
    today_str = date.today().isoformat()
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"eodhd_update_{today_str}.txt"

    logger = logging.getLogger("eodhd_updater")
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger


# ---------------------------------------------------------------------------
# Google Sheets helpers
# ---------------------------------------------------------------------------


def get_sheets_service():
    sa_value = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not sa_value:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON env var is not set")

    if sa_value.strip().startswith("{"):
        info = json.loads(sa_value)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES
        )
    else:
        creds = service_account.Credentials.from_service_account_file(
            sa_value, scopes=SCOPES
        )
    return build("sheets", "v4", credentials=creds)


def read_all_rows(service) -> list[list[str]]:
    """Return all rows from the AI Analysis sheet."""
    # Read enough columns to cover all defined columns
    end_col = _col_letter(len(ALL_COLUMNS) - 1)
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{SHEET_NAME}'!A1:{end_col}",
            valueRenderOption="FORMATTED_VALUE",
        )
        .execute()
    )
    return result.get("values", [])


def write_row_updates(service, updates: list[dict]):
    """Batch-write updates. Each entry: {"row": 1-indexed, "values": {col_letter: value}}."""
    if not updates:
        return

    data = []
    for upd in updates:
        row = upd["row"]
        for col_letter, value in upd["values"].items():
            data.append({
                "range": f"'{SHEET_NAME}'!{col_letter}{row}",
                "values": [[value]],
            })

    body = {"valueInputOption": "USER_ENTERED", "data": data}
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID, body=body
    ).execute()


def _col_letter(idx: int) -> str:
    """Convert 0-based column index to Excel-style letter(s). 0→A, 25→Z, 26→AA."""
    result = ""
    while True:
        result = chr(65 + idx % 26) + result
        idx = idx // 26 - 1
        if idx < 0:
            break
    return result


def _col_index(key: str) -> int:
    """Return the 0-based column index for a column key."""
    return ALL_COLUMNS.index(key)


def _col_letter_for_key(key: str) -> str:
    """Return the Excel column letter for a column key."""
    return _col_letter(_col_index(key))


# ---------------------------------------------------------------------------
# Sheet writing — headers, formatting, data
# ---------------------------------------------------------------------------


def write_ai_analysis_sheet(service, rows: list[dict], logger: logging.Logger):
    """Write the full AI Analysis sheet: group headers, column headers, data rows.

    `rows` is a list of dicts keyed by column keys from ALL_COLUMNS.
    """
    sheet_data = []

    # Row 1: Group headers
    group_header = []
    for group_name, _, cols in GROUPS:
        group_header.append(group_name)
        group_header.extend([""] * (len(cols) - 1))
    sheet_data.append(group_header)

    # Row 2: Column headers (display names)
    col_header = [DISPLAY_NAMES.get(c, c) for c in ALL_COLUMNS]
    sheet_data.append(col_header)

    # Data rows
    for row in rows:
        data_row = []
        for col_key in ALL_COLUMNS:
            val = row.get(col_key, "")
            if val is None:
                val = ""
            # Format percentage columns
            if col_key in PCT_COLS and isinstance(val, (int, float)):
                val = f"{val:.1f}%"
            elif col_key in DECIMAL_COLS and isinstance(val, (int, float)):
                val = f"{val:.1f}"
            elif col_key in DOLLAR_COLS and isinstance(val, (int, float)):
                val = f"${val:.2f}"
            data_row.append(val)
        sheet_data.append(data_row)

    # Write all at once
    end_col = _col_letter(len(ALL_COLUMNS) - 1)
    end_row = len(sheet_data)
    range_str = f"'{SHEET_NAME}'!A1:{end_col}{end_row}"

    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=range_str,
        valueInputOption="USER_ENTERED",
        body={"values": sheet_data},
    ).execute()

    logger.info("Wrote %d rows (incl. 2 header rows) to %s", len(sheet_data), range_str)


# ---------------------------------------------------------------------------
# EODHD API
# ---------------------------------------------------------------------------


def _fetch_fundamentals_raw(ticker: str, api_key: str, logger: logging.Logger) -> dict | None:
    """Fetch full fundamental data from EODHD for a US ticker."""
    symbol = f"{ticker}.US"
    url = f"{EODHD_BASE_URL}/{symbol}"
    params = {"api_token": api_key, "fmt": "json"}

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            logger.warning("Ticker %s not found on EODHD (404)", symbol)
        else:
            logger.warning("EODHD HTTP error for %s: %s", symbol, exc)
        return None
    except Exception as exc:
        logger.warning("EODHD request failed for %s: %s", symbol, exc)
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
        return v if v == v else None
    except (ValueError, TypeError):
        return None


def fmt_revenue(value: float | None) -> str:
    if value is None:
        return ""
    v = abs(value)
    sign = "-" if value < 0 else ""
    if v >= 1e9:
        return f"{sign}${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"{sign}${v / 1e6:.0f}M"
    if v >= 1e3:
        return f"{sign}${v / 1e3:.0f}K"
    return f"{sign}${v:.0f}"


def _sorted_entries(section: dict) -> list[tuple[str, dict]]:
    """Sort financial entries by date descending."""
    if not section:
        return []
    entries = [(k, v) for k, v in section.items() if isinstance(v, dict)]
    entries.sort(key=lambda x: x[0], reverse=True)
    return entries


# ---------------------------------------------------------------------------
# Core: fetch_eodhd_data — calculates all metrics for one ticker
# ---------------------------------------------------------------------------


def fetch_eodhd_data(ticker: str, api_key: str, logger: logging.Logger) -> dict | None:
    """Fetch EODHD fundamentals and compute all financial metrics.

    Returns a dict keyed by EODHD_COLUMNS column keys, or None on failure.
    """
    raw = _fetch_fundamentals_raw(ticker, api_key, logger)
    if raw is None:
        return None

    financials = raw.get("Financials", {})
    income_yearly = financials.get("Income_Statement", {}).get("yearly", {})
    income_quarterly = financials.get("Income_Statement", {}).get("quarterly", {})
    cashflow_quarterly = financials.get("Cash_Flow", {}).get("quarterly", {})
    earnings = raw.get("Earnings", {})

    yearly = _sorted_entries(income_yearly)
    quarterly = _sorted_entries(income_quarterly)
    cf_quarterly = _sorted_entries(cashflow_quarterly)
    eps_entries = _sorted_entries(earnings.get("History", {}))

    result = {}

    # ── Annual Revenue (5Y) ───────────────────────────────────────────
    annual_revs = []
    for date_str, entry in yearly[:5]:
        rev = safe_float(entry.get("totalRevenue"))
        if rev is not None:
            year = date_str[:4]
            annual_revs.append(f"{year}: {fmt_revenue(rev)}")
    result["annual_revenue_5y"] = " | ".join(annual_revs) if annual_revs else None

    # ── Quarterly Revenue ─────────────────────────────────────────────
    if quarterly:
        rev = safe_float(quarterly[0][1].get("totalRevenue"))
        q_date = quarterly[0][0]
        result["quarterly_revenue"] = f"{fmt_revenue(rev)} ({q_date})" if rev is not None else None
    else:
        result["quarterly_revenue"] = None

    # ── Rev Growth TTM % ──────────────────────────────────────────────
    rev_growth_ttm = None
    if len(quarterly) >= 8:
        recent_4 = sum(safe_float(e[1].get("totalRevenue")) or 0 for e in quarterly[:4])
        prior_4 = sum(safe_float(e[1].get("totalRevenue")) or 0 for e in quarterly[4:8])
        if prior_4 > 0:
            rev_growth_ttm = ((recent_4 - prior_4) / prior_4) * 100
    result["rev_growth_ttm"] = round(rev_growth_ttm, 1) if rev_growth_ttm is not None else None

    # ── Rev Growth QoQ % ──────────────────────────────────────────────
    if len(quarterly) >= 2:
        curr = safe_float(quarterly[0][1].get("totalRevenue"))
        prev = safe_float(quarterly[1][1].get("totalRevenue"))
        if curr is not None and prev and prev > 0:
            result["rev_growth_qoq"] = round(((curr - prev) / prev) * 100, 1)
        else:
            result["rev_growth_qoq"] = None
    else:
        result["rev_growth_qoq"] = None

    # ── Rev CAGR 3Y % ────────────────────────────────────────────────
    if len(yearly) >= 4:
        recent_rev = safe_float(yearly[0][1].get("totalRevenue"))
        base_rev = safe_float(yearly[3][1].get("totalRevenue"))
        if recent_rev and base_rev and base_rev > 0 and recent_rev > 0:
            cagr = ((recent_rev / base_rev) ** (1 / 3) - 1) * 100
            result["rev_cagr_3y"] = round(cagr, 1)
        else:
            result["rev_cagr_3y"] = None
    else:
        result["rev_cagr_3y"] = None

    # ── Rev Consistency Score (0–10) ──────────────────────────────────
    if len(quarterly) >= 2:
        n = min(len(quarterly) - 1, 10)
        growth_count = 0
        for i in range(n):
            c = safe_float(quarterly[i][1].get("totalRevenue"))
            p = safe_float(quarterly[i + 1][1].get("totalRevenue"))
            if c is not None and p is not None and c > p:
                growth_count += 1
        result["rev_consistency"] = f"{growth_count}/{n}"
    else:
        result["rev_consistency"] = None

    # ── Gross Margin TTM % ────────────────────────────────────────────
    gross_margin_ttm = None
    if len(quarterly) >= 4:
        gp_sum = sum(safe_float(e[1].get("grossProfit")) or 0 for e in quarterly[:4])
        rev_sum = sum(safe_float(e[1].get("totalRevenue")) or 0 for e in quarterly[:4])
        if rev_sum > 0:
            gross_margin_ttm = (gp_sum / rev_sum) * 100
    elif quarterly:
        gp = safe_float(quarterly[0][1].get("grossProfit"))
        rev = safe_float(quarterly[0][1].get("totalRevenue"))
        if gp is not None and rev and rev > 0:
            gross_margin_ttm = (gp / rev) * 100
    result["gross_margin_ttm"] = round(gross_margin_ttm, 1) if gross_margin_ttm is not None else None

    # ── GM Trend (Qtly) ──────────────────────────────────────────────
    gross_margin_trend = None
    if len(quarterly) >= 4:
        margins = []
        for entry in quarterly[:4]:
            gp = safe_float(entry[1].get("grossProfit"))
            rev = safe_float(entry[1].get("totalRevenue"))
            if gp is not None and rev and rev > 0:
                margins.append((gp / rev) * 100)
        if len(margins) >= 2:
            gross_margin_trend = margins[0] - margins[-1]  # pp change
            if gross_margin_trend > 1:
                result["gross_margin_trend"] = "↑"
            elif gross_margin_trend < -1:
                result["gross_margin_trend"] = "↓"
            else:
                result["gross_margin_trend"] = "→"
        else:
            result["gross_margin_trend"] = None
    else:
        result["gross_margin_trend"] = None

    # ── Operating Margin TTM % ────────────────────────────────────────
    if len(quarterly) >= 4:
        oi_sum = sum(safe_float(e[1].get("operatingIncome")) or 0 for e in quarterly[:4])
        rev_sum = sum(safe_float(e[1].get("totalRevenue")) or 0 for e in quarterly[:4])
        if rev_sum > 0:
            result["operating_margin_ttm"] = round((oi_sum / rev_sum) * 100, 1)
        else:
            result["operating_margin_ttm"] = None
    else:
        result["operating_margin_ttm"] = None

    # ── Net Margin TTM % ──────────────────────────────────────────────
    net_margin_ttm = None
    if len(quarterly) >= 4:
        ni_sum = sum(safe_float(e[1].get("netIncome")) or 0 for e in quarterly[:4])
        rev_sum = sum(safe_float(e[1].get("totalRevenue")) or 0 for e in quarterly[:4])
        if rev_sum > 0:
            net_margin_ttm = (ni_sum / rev_sum) * 100
    result["net_margin_ttm"] = round(net_margin_ttm, 1) if net_margin_ttm is not None else None

    # ── Net Margin YoY Δ ──────────────────────────────────────────────
    if len(quarterly) >= 5:
        def _nm(entry):
            ni = safe_float(entry.get("netIncome"))
            rev = safe_float(entry.get("totalRevenue"))
            if ni is not None and rev and rev > 0:
                return (ni / rev) * 100
            return None
        curr_nm = _nm(quarterly[0][1])
        yoy_nm = _nm(quarterly[4][1])
        if curr_nm is not None and yoy_nm is not None:
            result["net_margin_yoy_delta"] = round(curr_nm - yoy_nm, 1)
        else:
            result["net_margin_yoy_delta"] = None
    else:
        result["net_margin_yoy_delta"] = None

    # ── FCF Margin TTM % ──────────────────────────────────────────────
    fcf_margin_ttm = None
    if len(cf_quarterly) >= 4 and len(quarterly) >= 4:
        fcf_sum = sum(safe_float(e[1].get("freeCashFlow")) or 0 for e in cf_quarterly[:4])
        rev_sum = sum(safe_float(e[1].get("totalRevenue")) or 0 for e in quarterly[:4])
        if rev_sum > 0:
            fcf_margin_ttm = (fcf_sum / rev_sum) * 100
    elif cf_quarterly and quarterly:
        fcf = safe_float(cf_quarterly[0][1].get("freeCashFlow"))
        rev = safe_float(quarterly[0][1].get("totalRevenue"))
        if fcf is not None and rev and rev > 0:
            fcf_margin_ttm = (fcf / rev) * 100
    result["fcf_margin_ttm"] = round(fcf_margin_ttm, 1) if fcf_margin_ttm is not None else None

    # ── Opex % of Revenue ─────────────────────────────────────────────
    if quarterly:
        opex = safe_float(quarterly[0][1].get("totalOperatingExpenses"))
        rev = safe_float(quarterly[0][1].get("totalRevenue"))
        if opex is not None and rev and rev > 0:
            result["opex_pct_revenue"] = round((opex / rev) * 100, 1)
        else:
            result["opex_pct_revenue"] = None
    else:
        result["opex_pct_revenue"] = None

    # ── S&M+R&D % of Revenue ─────────────────────────────────────────
    if quarterly:
        latest = quarterly[0][1]
        sm = safe_float(latest.get("sellingAndMarketingExpenses")) or 0
        sga = safe_float(latest.get("sellingGeneralAdministrative")) or 0
        rd = safe_float(latest.get("researchDevelopment")) or 0
        sm_total = sm if sm else sga
        combined = sm_total + rd
        rev = safe_float(latest.get("totalRevenue"))
        if combined > 0 and rev and rev > 0:
            result["sm_rd_pct_revenue"] = round((combined / rev) * 100, 1)
        else:
            result["sm_rd_pct_revenue"] = None
    else:
        result["sm_rd_pct_revenue"] = None

    # ── Rule of 40 ────────────────────────────────────────────────────
    r40 = None
    if rev_growth_ttm is not None and net_margin_ttm is not None:
        r40 = rev_growth_ttm + net_margin_ttm
    result["rule_of_40"] = round(r40, 1) if r40 is not None else None

    # ── Qtrs to Profitability ─────────────────────────────────────────
    qtrs_to_prof = None
    if quarterly:
        latest_ni = safe_float(quarterly[0][1].get("netIncome"))
        if latest_ni is not None and latest_ni > 0:
            result["qtrs_to_profitability"] = "Profitable"
            qtrs_to_prof = 0
        elif latest_ni is not None:
            margins = []
            for entry in quarterly[:8]:
                ni = safe_float(entry[1].get("netIncome"))
                rev = safe_float(entry[1].get("totalRevenue"))
                if ni is not None and rev and rev > 0:
                    margins.append((ni / rev) * 100)
            if len(margins) >= 2:
                improvements = [margins[i] - margins[i + 1] for i in range(len(margins) - 1)]
                avg_improvement = sum(improvements) / len(improvements)
                if avg_improvement > 0:
                    qtrs_to_prof = int(-margins[0] / avg_improvement) + 1
                    if qtrs_to_prof > 20:
                        result["qtrs_to_profitability"] = ">20"
                    else:
                        result["qtrs_to_profitability"] = str(qtrs_to_prof)
                else:
                    result["qtrs_to_profitability"] = "N/A"
            else:
                result["qtrs_to_profitability"] = "N/A"
        else:
            result["qtrs_to_profitability"] = None
    else:
        result["qtrs_to_profitability"] = None

    # ── EPS Quarterly ─────────────────────────────────────────────────
    if eps_entries:
        eps_val = safe_float(eps_entries[0][1].get("epsActual"))
        result["eps_quarterly"] = eps_val
    else:
        result["eps_quarterly"] = None

    # ── EPS YoY % ─────────────────────────────────────────────────────
    if len(eps_entries) >= 5:
        curr_eps = safe_float(eps_entries[0][1].get("epsActual"))
        yoy_eps = safe_float(eps_entries[4][1].get("epsActual"))
        if curr_eps is not None and yoy_eps is not None and yoy_eps != 0:
            result["eps_yoy_pct"] = round(((curr_eps - yoy_eps) / abs(yoy_eps)) * 100, 1)
        else:
            result["eps_yoy_pct"] = None
    else:
        result["eps_yoy_pct"] = None

    # ── Margins improving flag (used by r40_score) ────────────────────
    margins_improving = False
    if gross_margin_trend is not None and gross_margin_trend > 1:
        margins_improving = True

    # ── R40 Score ─────────────────────────────────────────────────────
    # Gem scale calibrated to screened universe (GM>45%, RevGr>20%):
    #   no gems  = R40 < 20  (weakest in this universe)
    #   💎       = R40 20–39
    #   💎💎     = R40 40–59
    #   💎💎💎   = R40 60+
    r40 = result.get("rule_of_40")
    if r40 is not None:
        if r40 >= 60:
            gem_str = "\U0001f48e\U0001f48e\U0001f48e"   # 💎💎💎
        elif r40 >= 40:
            gem_str = "\U0001f48e\U0001f48e"              # 💎💎
        elif r40 >= 20:
            gem_str = "\U0001f48e"                        # 💎
        else:
            gem_str = ""                                  # no gems

        # FCF segment
        fcf = result.get("fcf_margin_ttm")
        fcf_str = f"FCF {'+' if fcf >= 0 else ''}{fcf:.0f}%" if fcf is not None else None

        # Profitability status segment
        nm = result.get("net_margin_ttm")
        qtrs = qtrs_to_prof
        if nm is not None and nm >= 0:
            profit_str = "\u2705"                          # ✅
        elif qtrs is not None:
            profit_str = f"\u23f3 ~{qtrs:.0f} qtrs GAAP"  # ⏳
        else:
            profit_str = "\u2753 path unclear"             # ❓

        r40_parts = [f"{gem_str} R40: {r40:.0f}".strip()]
        if fcf_str:
            r40_parts.append(fcf_str)
        r40_parts.append(profit_str)
        result["r40_score"] = " | ".join(r40_parts)

    # ── Fundamentals Snapshot ─────────────────────────────────────────
    # Format: Rev +27% YoY | 3Y CAGR +44% | GM 88% ↑ | Net -5% ⚡ FCF +27% | ⏳ ~12 qtrs GAAP
    #
    # Rules:
    # - All % values show explicit + or - sign
    # - GM trend arrow: ↑ if gross_margin_trend > 1pp, ↓ if < -1pp, → otherwise
    # - ⚡ appears between Net and FCF only when FCF exceeds Net margin by >15pp
    #   (signals: "accounting loss but cash-generative")
    # - Profitability segment reuses same logic as r40_score above
    snapshot_parts = []

    # Rev YoY
    if rev_growth_ttm is not None:
        sign = "+" if rev_growth_ttm >= 0 else ""
        snapshot_parts.append(f"Rev {sign}{rev_growth_ttm:.0f}% YoY")

    # 3Y CAGR
    cagr = result.get("rev_cagr_3y")
    if cagr is not None:
        sign = "+" if cagr >= 0 else ""
        snapshot_parts.append(f"3Y CAGR {sign}{cagr:.0f}%")

    # GM + trend arrow
    gm = result.get("gross_margin_ttm")
    gm_trend = gross_margin_trend  # raw pp change
    if gm is not None:
        if gm_trend is not None:
            if gm_trend > 1:
                arrow = " \u2191"    # ↑
            elif gm_trend < -1:
                arrow = " \u2193"    # ↓
            else:
                arrow = " \u2192"    # →
        else:
            arrow = ""
        snapshot_parts.append(f"GM {gm:.0f}%{arrow}")

    # Net margin + optional ⚡ + FCF margin
    nm = result.get("net_margin_ttm")
    fcf = result.get("fcf_margin_ttm")
    if nm is not None and fcf is not None:
        nm_sign = "+" if nm >= 0 else ""
        fcf_sign = "+" if fcf >= 0 else ""
        gap = fcf - nm
        if gap > 15:
            # FCF materially better than net margin — flag it
            snapshot_parts.append(
                f"Net {nm_sign}{nm:.0f}% \u26a1 FCF {fcf_sign}{fcf:.0f}%"  # ⚡
            )
        else:
            snapshot_parts.append(f"Net {nm_sign}{nm:.0f}% | FCF {fcf_sign}{fcf:.0f}%")
    elif nm is not None:
        nm_sign = "+" if nm >= 0 else ""
        snapshot_parts.append(f"Net {nm_sign}{nm:.0f}%")
    elif fcf is not None:
        fcf_sign = "+" if fcf >= 0 else ""
        snapshot_parts.append(f"FCF {fcf_sign}{fcf:.0f}%")

    # Profitability status (same logic as r40_score)
    if nm is not None and nm >= 0:
        snapshot_parts.append("\u2705")                           # ✅
    elif qtrs_to_prof is not None:
        snapshot_parts.append(f"\u23f3 ~{qtrs_to_prof:.0f} qtrs GAAP")  # ⏳
    else:
        snapshot_parts.append("\u2753 path unclear")              # ❓

    if snapshot_parts:
        result["fundamentals_snapshot"] = " | ".join(snapshot_parts)

    result["fundamentals_date"] = datetime.now().strftime("%Y-%m-%d")

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="EODHD Financial Data Updater")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and calculate but don't write to sheet")
    parser.add_argument("--ticker", type=str, default=None,
                        help="Process only this ticker (for testing)")
    parser.add_argument("--force", action="store_true",
                        help="Update all tickers even if data is recent")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N tickers (useful with --dry-run)")
    args = parser.parse_args()

    logger = setup_logging()
    logger.info("=== EODHD Updater started (dry_run=%s) ===", args.dry_run)
    start_time = time.time()

    # Validate EODHD key
    eodhd_key = os.environ.get("EODHD_API_KEY")
    if not eodhd_key:
        logger.error("EODHD_API_KEY env var is not set")
        sys.exit(1)

    # Read current sheet data
    service = get_sheets_service()
    all_rows = read_all_rows(service)
    logger.info("Read %d rows from sheet (including headers)", len(all_rows))

    # Build column mapping from current sheet headers
    # Row 2 (index 1) has column display names
    ai_col = {}  # display_name → column index
    if len(all_rows) >= 2:
        for idx, header in enumerate(all_rows[1]):
            ai_col[header.strip()] = idx

    # Data starts at row 3 (index 2)
    data_rows = all_rows[2:] if len(all_rows) > 2 else []

    # Build list of tickers to process
    ticker_col = ai_col.get("Ticker", 0)
    company_col = ai_col.get("Company", 1)
    fund_date_col = ai_col.get("Fundamentals Date")

    tickers_to_process = []
    for i, row in enumerate(data_rows):
        row_number = i + 3  # 1-indexed sheet row
        padded = row + [""] * (max(ticker_col, company_col, fund_date_col or 0) + 1 - len(row))
        ticker = padded[ticker_col].strip()
        company = padded[company_col].strip()

        if not ticker:
            continue

        if args.ticker and ticker.upper() != args.ticker.upper():
            continue

        # Skip if data is recent unless --force
        if not args.force and fund_date_col is not None:
            fund_date_str = padded[fund_date_col].strip() if fund_date_col < len(padded) else ""
            if fund_date_str:
                try:
                    last_date = dateparser.parse(fund_date_str).date()
                    if (date.today() - last_date) <= timedelta(days=STALENESS_DAYS):
                        logger.info("Skipping %s — data is recent (%s)", ticker, fund_date_str)
                        continue
                except (ValueError, TypeError):
                    pass

        tickers_to_process.append((row_number, ticker, company, row))

    if args.limit:
        tickers_to_process = tickers_to_process[:args.limit]

    logger.info("Will process %d tickers", len(tickers_to_process))

    if not tickers_to_process:
        logger.info("Nothing to do.")
        return

    # Process each ticker
    updates = []
    total_written = 0
    errors = 0

    for idx, (row_number, ticker, company, existing_row) in enumerate(tickers_to_process):
        try:
            eodhd_data = fetch_eodhd_data(ticker, eodhd_key, logger)
            if eodhd_data is None:
                errors += 1
                continue

            if args.dry_run:
                logger.info("[DRY RUN] %s (%s):", ticker, company)
                logger.info("  r40_score:             %s", eodhd_data.get("r40_score", ""))
                logger.info("  fundamentals_snapshot: %s", eodhd_data.get("fundamentals_snapshot", ""))
                for key in EODHD_COLUMNS:
                    if key not in ("r40_score", "fundamentals_snapshot"):
                        val = eodhd_data.get(key)
                        if val is not None:
                            display = DISPLAY_NAMES.get(key, key)
                            # Format for display
                            if key in PCT_COLS and isinstance(val, (int, float)):
                                logger.info("  %-25s %s%%", display, val)
                            elif key in DECIMAL_COLS and isinstance(val, (int, float)):
                                logger.info("  %-25s %s", display, val)
                            elif key in DOLLAR_COLS and isinstance(val, (int, float)):
                                logger.info("  %-25s $%s", display, val)
                            else:
                                logger.info("  %-25s %s", display, val)
                continue

            # Build update values with column letters
            values = {}
            for key in EODHD_COLUMNS:
                val = eodhd_data.get(key)
                if val is None:
                    continue
                col_letter = _col_letter_for_key(key)
                # Format value for sheet
                if key in PCT_COLS and isinstance(val, (int, float)):
                    values[col_letter] = f"{val:.1f}%"
                elif key in DECIMAL_COLS and isinstance(val, (int, float)):
                    values[col_letter] = f"{val:.1f}"
                elif key in DOLLAR_COLS and isinstance(val, (int, float)):
                    values[col_letter] = f"${val:.2f}"
                else:
                    values[col_letter] = str(val)

            if values:
                updates.append({"row": row_number, "values": values})
                logger.info("Calculated %d metrics for %s", len(values), ticker)

        except Exception as exc:
            logger.error("Error processing %s: %s", ticker, exc, exc_info=True)
            errors += 1

        # Write batch
        if updates and not args.dry_run and (len(updates) >= BATCH_SIZE or idx == len(tickers_to_process) - 1):
            logger.info("Writing batch of %d updates to sheet...", len(updates))
            write_row_updates(service, updates)
            total_written += len(updates)
            updates = []

        # Delay between API calls
        if idx < len(tickers_to_process) - 1:
            time.sleep(DELAY_BETWEEN_CALLS)

    elapsed = time.time() - start_time
    if args.dry_run:
        logger.info("=== DRY RUN complete. %d tickers processed in %.1fs ===",
                     len(tickers_to_process), elapsed)
    else:
        logger.info(
            "=== Updated %d tickers. Skipped %d due to errors. (%.1fs) ===",
            total_written, errors, elapsed,
        )


if __name__ == "__main__":
    main()
