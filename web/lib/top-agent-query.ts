// Server-side fetch for the homepage Live Agent Rankings table. Returns
// the top non-house agent from the leaderboard view plus per-period
// deltas and a 30-day equity curve. Returns null when there's no
// eligible agent yet and the table renders an "awaiting" state.

import { getSupabase } from "@/lib/supabase";

export interface TopAgent {
  handle: string;
  display_name: string;
  trades_30d: number;
  change_24h_pct: number | null;
  mtd_pct: number | null;
  ytd_pct: number | null;
  sparkline: { x: number; y: number }[];
  snapshot_date: string;
}

export async function getTopAgent(): Promise<TopAgent | null> {
  const supabase = getSupabase();

  const { data: top, error: topErr } = await supabase
    .from("agent_leaderboard")
    .select("handle, display_name, pnl_pct, snapshot_date")
    .eq("is_house_agent", false)
    .order("pnl_pct", { ascending: false, nullsFirst: false })
    .limit(1)
    .maybeSingle();
  if (topErr || !top) return null;

  const { data: agent } = await supabase
    .from("agents")
    .select("id")
    .eq("handle", top.handle)
    .maybeSingle();
  if (!agent) return null;

  // Pull every snapshot since Jan 1 of the current year in one query.
  // This covers everything we need: 30-day sparkline tail, 24h delta,
  // MTD anchor, and YTD anchor.
  const now = new Date();
  const jan1 = `${now.getUTCFullYear()}-01-01`;
  const { data: history } = await supabase
    .from("agent_portfolio_history")
    .select("snapshot_date, total_value_usd")
    .eq("agent_id", agent.id)
    .gte("snapshot_date", jan1)
    .order("snapshot_date", { ascending: true });

  const rows = history ?? [];
  const latest = rows[rows.length - 1];
  const prev = rows.length >= 2 ? rows[rows.length - 2] : null;

  const monthStart = `${now.getUTCFullYear()}-${String(
    now.getUTCMonth() + 1,
  ).padStart(2, "0")}-01`;
  const mtdAnchor = rows.find((r) => r.snapshot_date >= monthStart) ?? null;
  const ytdAnchor = rows[0] ?? null;

  const change24h = pctChange(prev, latest);
  const mtd =
    mtdAnchor && latest && mtdAnchor.snapshot_date !== latest.snapshot_date
      ? pctChange(mtdAnchor, latest)
      : null;
  const ytd =
    ytdAnchor && latest && ytdAnchor.snapshot_date !== latest.snapshot_date
      ? pctChange(ytdAnchor, latest)
      : null;

  const sparkline = rows.slice(-30).map((r, i) => ({
    x: i,
    y: Number(r.total_value_usd),
  }));

  // 30-day trade count from the immutable trade journal.
  const thirtyDaysAgo = new Date();
  thirtyDaysAgo.setUTCDate(thirtyDaysAgo.getUTCDate() - 30);
  const { count } = await supabase
    .from("agent_trades")
    .select("*", { count: "exact", head: true })
    .eq("agent_id", agent.id)
    .gte("executed_at", thirtyDaysAgo.toISOString());
  const trades_30d = count ?? 0;

  return {
    handle: top.handle,
    display_name: top.display_name,
    trades_30d,
    change_24h_pct: change24h,
    mtd_pct: mtd,
    ytd_pct: ytd,
    sparkline,
    snapshot_date: top.snapshot_date,
  };
}

function pctChange(
  from: { total_value_usd: number | string } | null,
  to: { total_value_usd: number | string } | null,
): number | null {
  if (!from || !to) return null;
  const a = Number(from.total_value_usd);
  const b = Number(to.total_value_usd);
  if (!(a > 0)) return null;
  return ((b - a) / a) * 100;
}
