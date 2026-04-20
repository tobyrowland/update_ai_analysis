// Server-side fetch for the homepage Live Agent Rankings table. Returns
// the single top non-house agent from the leaderboard view, plus a
// derived 24h change computed from the last two daily snapshots in
// agent_portfolio_history. Returns null when no eligible agent exists
// yet (early days) and the table renders an "awaiting" state.

import { getSupabase } from "@/lib/supabase";

export interface TopAgent {
  handle: string;
  display_name: string;
  total_return_pct: number | null;
  change_24h_pct: number | null;
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

  // 24h delta needs the two most recent snapshots; the leaderboard view
  // only exposes the latest, so we look up agent_id then pull two history
  // rows.
  const { data: agent } = await supabase
    .from("agents")
    .select("id")
    .eq("handle", top.handle)
    .maybeSingle();

  let change24h: number | null = null;
  if (agent) {
    const { data: history } = await supabase
      .from("agent_portfolio_history")
      .select("total_value_usd")
      .eq("agent_id", agent.id)
      .order("snapshot_date", { ascending: false })
      .limit(2);
    if (history && history.length === 2) {
      const curr = Number(history[0].total_value_usd);
      const prev = Number(history[1].total_value_usd);
      if (prev > 0) change24h = ((curr - prev) / prev) * 100;
    }
  }

  return {
    handle: top.handle,
    display_name: top.display_name,
    total_return_pct: top.pnl_pct == null ? null : Number(top.pnl_pct),
    change_24h_pct: change24h,
    snapshot_date: top.snapshot_date,
  };
}
