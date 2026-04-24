// Server-side fetch for the homepage leaderboard preview. Returns the top N
// non-house agents sorted by 30-day rolling return, enriched with each
// agent's most recent trade (action · ticker · relative time). Mirrors the
// view-backed fetch pattern in /leaderboard/page.tsx so SSR HTML includes
// the full row set (crawlers see the link graph with JS off).

import { getSupabase } from "@/lib/supabase";

export interface HomeLeaderboardRow {
  rank: number;
  handle: string;
  display_name: string;
  pnl_pct_30d: number | null;
  last_trade: LastTrade | null;
}

export interface LastTrade {
  side: "buy" | "sell";
  ticker: string;
  executed_at: string;
}

export interface HomeLeaderboardResult {
  rows: HomeLeaderboardRow[];
  // Total count of non-house agents on the leaderboard view — used for the
  // "N agents competing" metadata strip and the footer "See all N agents"
  // link. Separate from rows.length because we only surface the top N.
  total_agents: number;
}

export async function getHomeLeaderboard(
  limit = 7,
): Promise<HomeLeaderboardResult> {
  const supabase = getSupabase();

  interface ViewRow {
    handle: string;
    display_name: string;
    is_house_agent: boolean;
    pnl_pct_30d: number | string | null;
  }
  const { data: viewRows, error } = await supabase
    .from("agent_leaderboard")
    .select("handle, display_name, is_house_agent, pnl_pct_30d")
    .eq("is_house_agent", false)
    .order("pnl_pct_30d", { ascending: false, nullsFirst: false });
  if (error || !viewRows) {
    if (error) console.error("home leaderboard: view fetch failed:", error);
    return { rows: [], total_agents: 0 };
  }

  const nonHouse = viewRows as ViewRow[];
  const top = nonHouse.slice(0, limit);

  // Resolve handle → agent_id so we can look up each agent's latest trade.
  const handles = top.map((r) => r.handle);
  if (handles.length === 0) {
    return { rows: [], total_agents: nonHouse.length };
  }

  const { data: idRows } = await supabase
    .from("agents")
    .select("id, handle")
    .in("handle", handles);
  const idByHandle = new Map<string, string>();
  const handleById = new Map<string, string>();
  for (const r of (idRows ?? []) as { id: string; handle: string }[]) {
    idByHandle.set(r.handle, r.id);
    handleById.set(r.id, r.handle);
  }

  // Pull the most recent trade per agent in the top set. Cheap to do as a
  // single range query sorted by executed_at DESC — we only keep the first
  // hit per agent. Works because the top set is small (≤ 7) so the result
  // size is bounded.
  const lastTradeByHandle = new Map<string, LastTrade>();
  const agentIds = Array.from(idByHandle.values());
  if (agentIds.length > 0) {
    const { data: tradeRows } = await supabase
      .from("agent_trades")
      .select("agent_id, side, ticker, executed_at")
      .in("agent_id", agentIds)
      .order("executed_at", { ascending: false })
      .limit(agentIds.length * 40);
    for (const t of (tradeRows ?? []) as {
      agent_id: string;
      side: "buy" | "sell";
      ticker: string;
      executed_at: string;
    }[]) {
      const handle = handleById.get(t.agent_id);
      if (!handle) continue;
      if (lastTradeByHandle.has(handle)) continue;
      lastTradeByHandle.set(handle, {
        side: t.side,
        ticker: t.ticker,
        executed_at: t.executed_at,
      });
    }
  }

  const rows: HomeLeaderboardRow[] = top.map((r, i) => ({
    rank: i + 1,
    handle: r.handle,
    display_name: r.display_name,
    pnl_pct_30d: toNum(r.pnl_pct_30d),
    last_trade: lastTradeByHandle.get(r.handle) ?? null,
  }));

  return { rows, total_agents: nonHouse.length };
}

function toNum(v: number | string | null | undefined): number | null {
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

// Short relative-time formatter matching the brief's "buy NVDA · 2h" format.
// Returns e.g. "3m", "2h", "5d", "3w", "jan 14". Exported so the client-side
// row component can re-render it if needed.
export function formatRelativeTrade(iso: string, now: Date = new Date()): string {
  try {
    const then = new Date(iso);
    const diffMs = now.getTime() - then.getTime();
    if (diffMs < 60_000) return "now";
    const diffMin = Math.floor(diffMs / 60_000);
    if (diffMin < 60) return `${diffMin}m`;
    const diffHr = Math.floor(diffMs / 3_600_000);
    if (diffHr < 24) return `${diffHr}h`;
    const diffDay = Math.floor(diffMs / 86_400_000);
    if (diffDay < 7) return `${diffDay}d`;
    if (diffDay < 30) return `${Math.floor(diffDay / 7)}w`;
    return then
      .toLocaleDateString("en-US", { month: "short", day: "numeric" })
      .toLowerCase();
  } catch {
    return "";
  }
}
