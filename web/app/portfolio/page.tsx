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
            Portfolio
          </h1>
          <p className="text-sm text-text-muted font-mono">
            {companies.length} dual-positive equities (bear + bull pass)
          </p>
        </div>

        {companies.length === 0 ? (
          <div className="glass-card rounded-lg p-8 text-center">
            <p className="font-mono text-text-muted">
              No portfolio holdings found. Portfolio is built weekly from
              dual-positive (bear + bull) equities.
            </p>
          </div>
        ) : (
          <DataTable companies={companies} />
        )}
      </main>
    </>
  );
}
