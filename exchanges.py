"""
Consolidated exchange code mappings.

Single source of truth for exchange conversions used across the pipeline:
- TradingView → Google Finance (for ticker URLs)
- Spreadsheet/TV → EODHD (for fundamental data API)
- EODHD → Yahoo Finance suffix (for historical price data)
- Exchange fallback chains (when primary exchange returns 404)
"""

# ---------------------------------------------------------------------------
# TradingView → Google Finance exchange codes
# ---------------------------------------------------------------------------

TV_TO_GOOGLE_FINANCE = {
    # United States
    "NASDAQ": "NASDAQ", "NYSE": "NYSE", "NYSEARCA": "NYSEARCA",
    "NYSEMKT": "NYSEAMERICAN", "AMEX": "NYSEAMERICAN", "OTC": "OTCMKTS",
    "BATS": "BATS",
    # Canada
    "TSX": "TSE", "TSXV": "CVE",
    # United Kingdom
    "LSE": "LON", "LON": "LON", "LSIN": "LON",
    # Germany
    "XETRA": "ETR", "XETR": "ETR", "FRA": "FRA", "ETR": "ETR",
    "FWB": "FRA", "GETTEX": "ETR", "TRADEGATE": "ETR",
    "MU": "ETR", "STU": "ETR", "BE": "ETR", "DU": "ETR",
    "DUS": "ETR", "HM": "ETR", "HA": "ETR",
    # France
    "EPA": "EPA", "PAR": "EPA",
    # Netherlands
    "AMS": "AMS",
    # Switzerland
    "SWX": "SWX",
    # Italy
    "BIT": "BIT", "MIL": "BIT", "EUROTLX": "BIT",
    # Spain
    "BME": "BME",
    # Sweden
    "STO": "STO",
    # Norway
    "OSL": "OSL",
    # Denmark
    "CSE": "CPH",
    # Finland
    "HEL": "HEL",
    # Japan
    "TSE": "TYO", "JPX": "TYO", "TYO": "TYO",
    # India
    "NSE": "NSE", "BSE": "BOM", "NSEI": "NSE",
    # South Korea
    "KRX": "KRX", "KOSDAQ": "KRX",
    # Australia
    "ASX": "ASX",
    # New Zealand
    "NZX": "NZE",
    # Singapore
    "SGX": "SGX",
    # Hong Kong
    "HKG": "HKG", "HKEX": "HKG",
    # Brazil
    "SAO": "BVMF", "BVMF": "BVMF",
    # South Africa
    "JSE": "JSE",
    # Saudi Arabia
    "TADAWUL": "TADAWUL", "SAU": "TADAWUL",
    # Israel
    "TASE": "TLV",
    # Turkey
    "BIST": "IST",
    # Indonesia
    "IDX": "IDX",
    # Thailand
    "SET": "BKK",
    # Malaysia
    "MYX": "KLSE",
    # Philippines
    "PSE": "PSE",
    # Mexico
    "BMV": "BMV",
    # Poland
    "GPW": "WSE",
}


# ---------------------------------------------------------------------------
# Spreadsheet / TradingView → EODHD suffix codes
# ---------------------------------------------------------------------------

EXCHANGE_TO_EODHD = {
    # United States
    "NASDAQ": "US", "NYSE": "US", "NYSEARCA": "US", "NYSEMKT": "US",
    "AMEX": "US", "OTC": "US", "BATS": "US", "US": "US",
    # United Kingdom
    "LSE": "LSE", "LON": "LSE", "LONDON": "LSE",
    # India
    "NSE": "NSE", "BSE": "BSE", "NSEI": "NSE",
    # Japan
    "TSE": "TSE", "TYO": "TSE", "JPX": "TSE",
    # Germany
    "XETRA": "XETRA", "FRA": "F", "ETR": "XETRA",
    "GETTEX": "MU", "MU": "MU", "STU": "STU",
    "BE": "BE", "DU": "DU", "HM": "HM", "HA": "HA", "FWB": "F",
    "TRADEGATE": "MU",
    # Other Europe
    "EPA": "PA", "PAR": "PA", "AMS": "AS", "SWX": "SW",
    "BIT": "MI", "BME": "MC", "MIL": "MI", "VIE": "VI",
    "EURONEXT": "PA",
    # Scandinavia
    "STO": "ST", "OSL": "OL", "CSE": "CO", "HEL": "HE",
    # Asia-Pacific
    "HKG": "HK", "HKEX": "HK", "KRX": "KO", "KOSDAQ": "KO",
    "TWSE": "TW", "TPE": "TW", "SGX": "SG", "ASX": "AU", "NZX": "NZ",
    "MYX": "KL", "DFM": "AE",
    # Americas
    "TSX": "TO", "TSXV": "V", "SAO": "SA", "BVMF": "SA", "BMV": "MX",
    # Africa / Middle East
    "JSE": "JSE", "TADAWUL": "SR", "SAU": "SR",
    "NSENG": "NSENG", "NGS": "NSENG", "NGSE": "NSENG",
    # Israel / Turkey
    "TASE": "TA", "BIST": "IS",
    # Indonesia / Thailand / Philippines
    "IDX": "JK", "SET": "BK", "PSE": "PSE",
}


# ---------------------------------------------------------------------------
# EODHD exchange fallback chains
# ---------------------------------------------------------------------------

EXCHANGE_FALLBACKS = {
    # German regional exchanges → try XETRA, Frankfurt, then US
    "MU":    ["XETRA", "F", "US"],
    "STU":   ["XETRA", "F", "US"],
    "BE":    ["XETRA", "F", "US"],
    "DU":    ["XETRA", "F", "US"],
    "HM":    ["XETRA", "F", "US"],
    "HA":    ["XETRA", "F", "US"],
    "XETRA": ["F", "US"],
    "F":     ["XETRA", "US"],
    # India
    "BSE":   ["NSE", "US"],
    "NSE":   ["BSE", "US"],
    # Japan
    "TSE":   ["US"],
    # Switzerland
    "SW":    ["US"],
    # Netherlands / Euronext
    "AS":    ["US", "PA"],
    # Other Europe
    "PA":    ["US", "AS"],
    "MI":    ["US"],
    "MC":    ["US"],
    # UK
    "LSE":   ["US"],
    # Canada
    "TO":    ["V", "US"],
    "V":     ["TO", "US"],
    # Hong Kong
    "HK":    ["US"],
    # Korea
    "KO":    ["US"],
    # Australia
    "AU":    ["US"],
    # Nigeria
    "NSENG": ["LSE", "US"],
}

# Ensure US is always a last-ditch fallback.
for _exc, _chain in EXCHANGE_FALLBACKS.items():
    if "US" not in _chain:
        _chain.append("US")


# ---------------------------------------------------------------------------
# EODHD code → Yahoo Finance ticker suffix (for historical prices)
# ---------------------------------------------------------------------------

YAHOO_SUFFIX = {
    "US": "", "NSE": ".NS", "BSE": ".BO",
    "XETRA": ".DE", "F": ".F", "MU": ".MU", "STU": ".SG",
    "BE": ".BE", "DU": ".DU", "HM": ".HM", "HA": ".HA",
    "PA": ".PA", "AS": ".AS", "SW": ".SW", "MI": ".MI", "MC": ".MC",
    "VI": ".VI",
    "LSE": ".L", "HK": ".HK", "KO": ".KS", "TW": ".TW",
    "SG": ".SI", "AU": ".AX", "NZ": ".NZ", "KL": ".KL", "AE": ".AE",
    "TO": ".TO", "V": ".V", "SA": ".SA", "MX": ".MX",
    "TSE": ".T", "JSE": ".JO", "SR": ".SR",
    "NSENG": ".LG",
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def resolve_eodhd_exchange(exchange: str) -> str:
    """Convert a spreadsheet/TV exchange name to the EODHD suffix code."""
    key = exchange.strip().upper()
    return EXCHANGE_TO_EODHD.get(key, key)


def google_finance_url(ticker: str, exchange: str) -> str:
    """Build a Google Finance URL for a ticker."""
    gf_exchange = TV_TO_GOOGLE_FINANCE.get(exchange.upper(), exchange)
    return f"https://www.google.com/finance/quote/{ticker}:{gf_exchange}"
