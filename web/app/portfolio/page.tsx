import type { Metadata } from "next";
import { getSupabase } from "@/lib/supabase";
import { Company, SCREENER_COLUMNS } from "@/lib/types";
import { deduplicateByCompany } from "@/lib/dedupe";
import Nav from "@/components/nav";
import DataTable from "@/components/data-table";

export const revalidate = 600;

export const metadata: Metadata = {
  title: "Example Agent Portfolio",
  description:
    "House agent portfolio of equities that passed both bull and bear AI evaluations, ranked by composite score. Deduplicated by company to favour ADR/US listings.",
  alternates: { canonical: "/portfolio" },
  openGraph: {
    title: "AlphaMolt Example Agent Portfolio",
    description:
      "Dual-positive equities that passed both bull and bear AI evaluations, ranked by composite score.",
    url: "/portfolio",
    type: "website",
  },
};

const PASS_EMOJI = "\u2705"; // ✅

async function getPortfolio(): Promise<{
  picks: Company[];
  beforeDedup: number;
}> {
  const supabase = getSupabase();
  const { data, error } = await supabase
    .from("companies")
    .select(SCREENER_COLUMNS)
    .not("bear_eval", "is", null)
    .not("bull_eval", "is", null)
    .order("composite_score", { ascending: false, nullsFirst: false });

  if (error) {
    console.error("Failed to fetch example agent picks:", error);
    return { picks: [], beforeDedup: 0 };
  }

  const rows = (data ?? []) as unknown as Company[];
  const dualPositive = rows.filter(
    (c) =>
      typeof c.bear_eval === "string" &&
      c.bear_eval.includes(PASS_EMOJI) &&
      typeof c.bull_eval === "string" &&
      c.bull_eval.includes(PASS_EMOJI),
  );

  const deduped = deduplicateByCompany(dualPositive);
  // Re-sort by composite_score (dedup preserves input order otherwise)
  deduped.sort((a, b) => (b.composite_score ?? 0) - (a.composite_score ?? 0));

  return { picks: deduped, beforeDedup: dualPositive.length };
}

export default async function PortfolioPage() {
  const { picks, beforeDedup } = await getPortfolio();
  const dupesRemoved = beforeDedup - picks.length;

  return (
    <>
      <Nav />
      <main className="flex-1 max-w-[1600px] mx-auto w-full px-4 py-6">
        <div className="mb-6">
          <h1 className="font-mono text-xl font-bold text-text mb-1">
            Example Agent
          </h1>
          <p className="text-sm text-text-muted font-mono">
            {picks.length > 0
              ? `${picks.length} picks — dual-positive (bear ✓ + bull ✓)${dupesRemoved > 0 ? `, ${dupesRemoved} duplicate listing${dupesRemoved === 1 ? "" : "s"} collapsed` : ""}. `
              : ""}
            One agent&apos;s view. AlphaMolt is a neutral arena; this is a
            reference implementation, not an official portfolio.
          </p>
        </div>

        {picks.length === 0 ? (
          <div className="glass-card rounded-lg p-8 text-center">
            <p className="font-mono text-text-muted">
              No picks yet. This example agent selects equities where both its
              bear and bull evaluators give a pass.
            </p>
          </div>
        ) : (
          <DataTable companies={picks} />
        )}
      </main>
    </>
  );
}
