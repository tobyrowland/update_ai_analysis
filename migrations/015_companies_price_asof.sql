-- Migration 015: add `price_asof` to companies for 15-min delayed quotes.
--
-- companies.price is now refreshed every 15 minutes during US market hours
-- by intraday_prices.py (in addition to the daily score_ai_analysis.py /
-- nightly_screen.py paths). price_asof records when the value last landed
-- so the UI can show "15-min delayed · last refresh 14:32 UTC".
--
-- Outside market hours price_asof points at the prior trading day's last
-- intraday tick (~21:45 UTC, which captures the close once delays settle).

ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS price_asof TIMESTAMPTZ;

COMMENT ON COLUMN companies.price_asof IS
    'When companies.price was last refreshed. 15-min delayed intraday during US market hours via intraday_prices.py; close-of-business otherwise.';
