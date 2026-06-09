import { getSupabase } from "@/lib/supabase";

export interface ScreenerExclusion {
  ticker: string;
  expires_at: string;
}

/** Active (non-expired) screener exclusions — the manual 1-year blocklist
 *  (migration 048). Drives the owner's "hidden names" manage panel. */
export async function listActiveExclusions(): Promise<ScreenerExclusion[]> {
  const { data, error } = await getSupabase()
    .from("screener_exclusions")
    .select("ticker, expires_at")
    .gt("expires_at", new Date().toISOString())
    .order("excluded_at", { ascending: false });
  if (error) {
    console.error("listActiveExclusions failed:", error);
    return [];
  }
  return (data ?? []) as ScreenerExclusion[];
}
