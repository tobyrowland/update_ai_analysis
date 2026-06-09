"use server";

/**
 * Server actions for the screener's manual 1-year blocklist (migration 048).
 * Auth-gated (a signed-in user), then service-role write — same verify-then-
 * service-role pattern as portfolios-mutations. The exclusion is global: it
 * drops the name from the screener AND the buyer's candidate pool (screen.py),
 * so the agents won't buy it either, until it expires.
 */

import { revalidatePath } from "next/cache";
import { getSupabase } from "@/lib/supabase";
import { requireUser } from "@/lib/auth/require-user";

export type ExclusionResult = { ok: true } | { ok: false; error: string };

const YEAR_MS = 365 * 24 * 60 * 60 * 1000;

export async function excludeFromScreener(
  ticker: string,
): Promise<ExclusionResult> {
  const { user } = await requireUser();
  const t = ticker.trim().toUpperCase();
  if (!t) return { ok: false, error: "Ticker required." };

  const now = new Date();
  const { error } = await getSupabase().from("screener_exclusions").upsert(
    {
      ticker: t,
      excluded_at: now.toISOString(),
      expires_at: new Date(now.getTime() + YEAR_MS).toISOString(),
      created_by: user.id,
    },
    { onConflict: "ticker" },
  );
  if (error) {
    console.error("excludeFromScreener failed:", error);
    return { ok: false, error: "Could not remove it. Try again." };
  }
  revalidatePath("/screener");
  return { ok: true };
}

export async function unexcludeFromScreener(
  ticker: string,
): Promise<ExclusionResult> {
  await requireUser();
  const t = ticker.trim().toUpperCase();
  const { error } = await getSupabase()
    .from("screener_exclusions")
    .delete()
    .eq("ticker", t);
  if (error) {
    console.error("unexcludeFromScreener failed:", error);
    return { ok: false, error: "Could not restore it. Try again." };
  }
  revalidatePath("/screener");
  return { ok: true };
}
