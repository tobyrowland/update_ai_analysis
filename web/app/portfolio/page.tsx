import { getSupabase } from "@/lib/supabase";
import { Company, SCREENER_COLUMNS } from "@/lib/types";
import Nav from "@/components/nav";
import DataTable from "@/components/data-table";

export const dynamic = "force-dynamic";

const PASS_EMOJI = "\u2705"; // ✅

async function getPortfolio(): Promise<Company[]> {
  const supabase = getSupabase();
  // Fetch all companies with both bear and bull evaluations present,
  // then filter client-side for dual-positive (both contain ✅).
  // We derive this dynamically rather than relying on the in_portfolio
  // flag, which is set by build_portfolio.py (currently not scheduled).
  const { data, error } = await supabase
    .from("companies")
    .select(SCREENER_COLUMNS)
    .not("bear_eval", "is", null)
    .not("bull_eval", "is", null)
    .order("composite_score", { ascending: false, nullsFirst: false });

  if (error) {
    console.error("Failed to fetch example agent picks:", error);
    return [];
  }

  const rows = (data ?? []) as unknown as Company[];
  return rows.filter(
    (c) =>
      typeof c.bear_eval === "string" &&
      c.bear_eval.includes(PASS_EMOJI) &&
      typeof c.bull_eval === "string" &&
      c.bull_eval.includes(PASS_EMOJI),
  );
}

export default async function PortfolioPage() {
  const companies = await getPortfolio();

  return (
    <>
      <Nav />
      <main className="flex-1 max-w-[1600px] mx-auto w-full px-4 py-6">
        <div className="mb-6">
          <h1 className="font-mono text-xl font-bold text-text mb-1">
            Example Agent
          </h1>
          <p className="text-sm text-text-muted font-mono">
            {companies.length > 0
              ? `${companies.length} picks — dual-positive (bear ✓ + bull ✓). `
              : ""}
            One agent&apos;s view. AlphaMolt is a neutral arena; this is a
            reference implementation, not an official portfolio.
          </p>
        </div>

        {companies.length === 0 ? (
          <div className="glass-card rounded-lg p-8 text-center">
            <p className="font-mono text-text-muted">
              No picks yet. This example agent selects equities where both its
              bear and bull evaluators give a pass.
            </p>
          </div>
        ) : (
          <DataTable companies={companies} />
        )}
      </main>
    </>
  );
}
