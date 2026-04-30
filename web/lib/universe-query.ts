/**
 * Supabase query wrapper for universe_snapshots.
 *
 * Snapshots are written daily by build_universe_snapshot.py, one row per
 * (date, detail) tier. This module is the single read-side entry point —
 * the public /api/v1/universe endpoint, the /universe page, and (later)
 * the llm_pick strategy all funnel through here.
 *
 * Slicing model: tier selection happens at row-level (one DB hit per
 * request), ticker slicing happens in-memory after the JSON is loaded.
 * That's cheap because the largest tier is ~1MB and slicing is a
 * filter+filter on a smallish array. If snapshots ever grow past ~5MB
 * we'd revisit.
 */

import { getSupabase } from "@/lib/supabase";

export type Detail = "compact" | "extended" | "full";

export const DETAILS: readonly Detail[] = ["compact", "extended", "full"];

export interface SnapshotMeta {
  snapshot_date: string;     // ISO date "YYYY-MM-DD"
  detail: Detail;
  sha256: string;
  ticker_count: number;
  created_at: string;
}

export interface SnapshotJson {
  snapshot_date: string;
  snapshot_time_utc: string;
  detail: Detail;
  universe_filter: Record<string, unknown>;
  ticker_count: number;
  tickers: Array<Record<string, unknown> & { ticker?: string }>;
}

export interface SnapshotResponse extends SnapshotMeta {
  json: SnapshotJson;
}

export function isDetail(s: string | null | undefined): s is Detail {
  return s === "compact" || s === "extended" || s === "full";
}

/**
 * Parse a `?tickers=NVDA,AAPL,...` query value into a normalised set.
 * Returns null if the param is empty/missing — caller treats that as
 * "no slicing, return everything".
 */
export function parseTickerFilter(raw: string | null): Set<string> | null {
  if (!raw) return null;
  const tickers = raw
    .split(",")
    .map((t) => t.trim().toUpperCase())
    .filter(Boolean);
  return tickers.length > 0 ? new Set(tickers) : null;
}

/**
 * Fetch the latest snapshot at the given detail tier. Returns null if no
 * snapshot has been built yet (fresh deploy, before the first 06:00 UTC
 * cron) or if the table is empty.
 */
export async function getLatestSnapshot(
  detail: Detail,
): Promise<SnapshotResponse | null> {
  const supabase = getSupabase();
  const { data, error } = await supabase
    .from("universe_snapshots")
    .select("snapshot_date, detail, sha256, ticker_count, created_at, json")
    .eq("detail", detail)
    .order("snapshot_date", { ascending: false })
    .limit(1)
    .maybeSingle();
  if (error) {
    console.error("getLatestSnapshot query failed:", error);
    return null;
  }
  return data ? (data as unknown as SnapshotResponse) : null;
}

/**
 * Fetch a specific historical snapshot. Returns null when the (date,
 * detail) pair doesn't exist — the API surface translates that into 404.
 */
export async function getSnapshotByDate(
  date: string,
  detail: Detail,
): Promise<SnapshotResponse | null> {
  const supabase = getSupabase();
  const { data, error } = await supabase
    .from("universe_snapshots")
    .select("snapshot_date, detail, sha256, ticker_count, created_at, json")
    .eq("snapshot_date", date)
    .eq("detail", detail)
    .maybeSingle();
  if (error) {
    console.error("getSnapshotByDate query failed:", error);
    return null;
  }
  return data ? (data as unknown as SnapshotResponse) : null;
}

/**
 * List the most recent N snapshot dates (for the date-picker UI). Reads
 * the `compact` tier only since dates are always built in lockstep across
 * tiers — one row per tier per day.
 */
export async function listSnapshotDates(limit = 30): Promise<SnapshotMeta[]> {
  const supabase = getSupabase();
  const { data, error } = await supabase
    .from("universe_snapshots")
    .select("snapshot_date, detail, sha256, ticker_count, created_at")
    .eq("detail", "compact")
    .order("snapshot_date", { ascending: false })
    .limit(limit);
  if (error) {
    console.error("listSnapshotDates query failed:", error);
    return [];
  }
  return (data ?? []) as SnapshotMeta[];
}

/**
 * Apply an in-memory ticker filter to a snapshot. The returned object
 * keeps every top-level key but the `tickers` array and `ticker_count`
 * are restricted. Use this when callers pass `?tickers=...` so the
 * payload shrinks before serialisation.
 */
export function sliceByTickers(
  snapshot: SnapshotJson,
  tickers: Set<string>,
): SnapshotJson {
  const filtered = snapshot.tickers.filter((t) => {
    const sym = String(t.ticker ?? "").toUpperCase();
    return tickers.has(sym);
  });
  return {
    ...snapshot,
    tickers: filtered,
    ticker_count: filtered.length,
  };
}
