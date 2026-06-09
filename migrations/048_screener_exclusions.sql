-- Migration 048: screener exclusions (manual 1-year blocklist).
--
-- A manually-removed company stays out of BOTH the screener results and the
-- buyer's candidate pool (top-N of the screen) for a year — so the agents won't
-- buy it either. Operator-curated and global (the screener is the house research
-- surface; the buyer is the house pipeline), expiring automatically.
--
-- Applied at read time:
--   * web/lib/screen/query.ts runScreen()  -> filters the screener
--   * screen.py load_facts()               -> filters the Python buyer
-- both drop tickers whose exclusion hasn't expired.
--
-- Public-read so those read paths (and a logged-out screener) can filter;
-- writes are service-role only (the owner UI calls a server action that checks
-- auth first, then writes service-role) — there's no anon/auth write policy.
--
-- Additive & idempotent.

CREATE TABLE IF NOT EXISTS screener_exclusions (
    ticker      TEXT PRIMARY KEY,
    excluded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL,
    note        TEXT,
    created_by  UUID REFERENCES auth.users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Active-exclusion lookups filter on expires_at > now().
CREATE INDEX IF NOT EXISTS idx_screener_exclusions_expires
    ON screener_exclusions (expires_at);

ALTER TABLE screener_exclusions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "public read screener_exclusions" ON screener_exclusions;
CREATE POLICY "public read screener_exclusions"
    ON screener_exclusions FOR SELECT USING (true);
-- No INSERT/UPDATE/DELETE policy → writes are service-role only.
