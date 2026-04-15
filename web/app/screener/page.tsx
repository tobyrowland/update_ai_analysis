import type { Metadata } from "next";
import { getSupabase } from "@/lib/supabase";
import { Company, SCREENER_COLUMNS } from "@/lib/types";
import Nav from "@/components/nav";
import DataTable from "@/components/data-table";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Screener — 400+ global growth stocks",
  description:
    "Browse 400+ global growth stocks ranked by composite score. Nightly TradingView screen filtered by market cap, gross margin, revenue growth, and Rule of 40. Fundamentals and AI narratives refreshed daily.",
  alternates: { canonical: "/screener" },
  openGraph: {
    title: "AlphaMolt Screener — 400+ global growth stocks",
    description:
      "Browse 400+ global growth stocks ranked by composite score. Nightly screen, fundamentals, and AI narratives refreshed daily.",
    url: "/screener",
    type: "website",
  },
};

async function getCompanies(): Promise<Company[]> {
  const supabase = getSupabase();
  const { data, error } = await supabase
    .from("companies")
    .select(SCREENER_COLUMNS)
    .order("sort_order", { ascending: true, nullsFirst: false });

  if (error) {
    console.error("Failed to fetch companies:", error);
    return [];
  }

  return (data ?? []) as unknown as Company[];
}

export default async function ScreenerPage() {
  const companies = await getCompanies();

  return (
    <>
      <Nav />
      <main className="flex-1 max-w-[1600px] mx-auto w-full px-4 py-6">
        <div className="mb-6">
          <h1 className="font-mono text-xl font-bold text-text mb-1">
            Screener
          </h1>
          <p className="text-sm text-text-muted font-mono">
            {companies.length} equities tracked across 35+ global markets
          </p>
        </div>
        <DataTable companies={companies} />
      </main>
    </>
  );
}
