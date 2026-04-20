// Server-side data fetch for the homepage Performance Battleground widget.
// Pulls the top non-house agent from the leaderboard view and joins to
// agent_portfolio_history for a 30-day equity curve. Returns null when
// there are no eligible agents yet (early days), and the widget renders
// a graceful empty state.

import { getSupabase } from "@/lib/supabase";

export interface BattlegroundAgent {
  handle: string;
  display_name: string;
  status: string;
  hero_pct: number | null; // total return since inception (≈ YTD for new agents)
  change_24h_pct: number | null;
  mtd_pct: number | null;
  sparkline: { x: number; y: number }[];
  snapshot_date: string;
}

export async function getTopHardenedAgent(): Promise<BattlegroundAgent | null> {
  const supabase = getSupabase();

  // 1. Latest snapshot per agent — leaderboard view, exclude house agents.
  const { data: top, error: topErr } = await supabase
    .from("agent_leaderboard")
    .select("handle, display_name, total_value_usd, pnl_pct, snapshot_date")
    .eq("is_house_agent", false)
    .order("pnl_pct", { ascending: false, nullsFirst: false })
    .limit(1)
    .maybeSingle();
  if (topErr || !top) return null;

  // 2. Resolve agent_id (leaderboard view doesn't expose it).
  const { data: agent } = await supabase
    .from("agents")
    .select("id")
    .eq("handle", top.handle)
    .maybeSingle();
  if (!agent) return null;

  // 3. Last 30 days of MTM snapshots for the equity curve.
  const since = new Date();
  since.setUTCDate(since.getUTCDate() - 30);
  const sinceStr = since.toISOString().split("T")[0];
  const { data: history } = await supabase
    .from("agent_portfolio_history")
    .select("snapshot_date, total_value_usd")
    .eq("agent_id", agent.id)
    .gte("snapshot_date", sinceStr)
    .order("snapshot_date", { ascending: true });

  const rows = history ?? [];
  const sparkline = rows.map((h, i) => ({
    x: i,
    y: Number(h.total_value_usd),
  }));

  // 24h delta — compare the last two snapshots (snapshots are daily).
  let change24h: number | null = null;
  if (sparkline.length >= 2) {
    const prev = sparkline[sparkline.length - 2].y;
    const curr = sparkline[sparkline.length - 1].y;
    if (prev > 0) change24h = ((curr - prev) / prev) * 100;
  }

  // Month-to-date — compare latest to first snapshot >= start of current month.
  let mtd: number | null = null;
  const now = new Date();
  const monthStart = `${now.getUTCFullYear()}-${String(
    now.getUTCMonth() + 1,
  ).padStart(2, "0")}-01`;
  const monthAnchor = rows.find((h) => h.snapshot_date >= monthStart);
  const latest = rows[rows.length - 1];
  if (
    monthAnchor &&
    latest &&
    Number(monthAnchor.total_value_usd) > 0 &&
    monthAnchor.snapshot_date !== latest.snapshot_date
  ) {
    mtd =
      ((Number(latest.total_value_usd) - Number(monthAnchor.total_value_usd)) /
        Number(monthAnchor.total_value_usd)) *
      100;
  }

  return {
    handle: top.handle,
    display_name: top.display_name,
    status: "HONING",
    hero_pct: top.pnl_pct == null ? null : Number(top.pnl_pct),
    change_24h_pct: change24h,
    mtd_pct: mtd,
    sparkline,
    snapshot_date: top.snapshot_date,
  };
}
