-- Migration 010: Long-form description for agents
--
-- Existing `description` column is the one-line tagline shown on the
-- leaderboard and inline on the profile page (capped at 500 chars by
-- the createAgent validator). `long_description` is the optional
-- explainer rendered in a collapsible "Strategy" panel on the agent
-- detail page — room for the rebalance algorithm, model lineage, etc.

ALTER TABLE agents
  ADD COLUMN IF NOT EXISTS long_description TEXT;
