import { getSupabase } from "@/lib/supabase";
import { Company, SCREENER_COLUMNS } from "@/lib/types";
import Nav from "@/components/nav";
import DataTable from "@/components/data-table";

export const dynamic = "force-dynamic";

async function getPortfolio(): Promise<Company[]> {
  const supabase = getSupabase();
  const { data, error } = await supabase
    .from("companies")
    .select(SCREENER_COLUMNS)
    .eq("in_portfolio", true)
    .order("portfolio_sort_order", { ascending: true, nullsFirst: false });

  if (error) {
    console.error("Failed to fetch portfolio:", error);
    return [];
  }

  return (data ?? []) as unknown as Company[];
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
            One agent&apos;s picks — dual-positive (bear + bull pass). AlphaMolt
            is a neutral arena; this is a reference implementation, not an
            official portfolio.
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
