/**
 * Shared Supabase query logic for equities.
 *
 * Used by both the REST v1 API routes and the MCP tool handlers so the
 * two surfaces return identical shapes and respect identical limits.
 */

import { getSupabase } from "@/lib/supabase";
import { Company, PriceSales, SCREENER_COLUMNS } from "@/lib/types";

export interface EquityListFilters {
  status?: string | null;
  sector?: string | null;
  country?: string | null;
  limit?: number | null;
  offset?: number | null;
}

export const DEFAULT_LIMIT = 1000;
export const MAX_LIMIT = 1000;

export interface EquityListResult {
  equities: Partial<Company>[];
  count: number;
  limit: number;
  offset: number;
}

export async function listEquities(
  filters: EquityListFilters = {},
): Promise<EquityListResult> {
  const limit = Math.min(
    Math.max(Number(filters.limit ?? DEFAULT_LIMIT) || DEFAULT_LIMIT, 1),
    MAX_LIMIT,
  );
  const offset = Math.max(Number(filters.offset ?? 0) || 0, 0);

  const supabase = getSupabase();
  let query = supabase
    .from("companies")
    .select(SCREENER_COLUMNS)
    .order("sort_order", { ascending: true, nullsFirst: false });

  if (filters.status) {
    query = query.ilike("status", `%${filters.status}%`);
  }
  if (filters.sector) {
    query = query.eq("sector", filters.sector);
  }
  if (filters.country) {
    query = query.eq("country", filters.country);
  }

  query = query.range(offset, offset + limit - 1);

  const { data, error } = await query;
  if (error) {
    throw new Error(`Supabase query failed: ${error.message}`);
  }

  const rows = (data ?? []) as unknown as Partial<Company>[];
  return {
    equities: rows,
    count: rows.length,
    limit,
    offset,
  };
}

export interface EquityDetailResult {
  company: Company;
  price_sales: PriceSales | null;
}

export async function getEquity(
  ticker: string,
): Promise<EquityDetailResult | null> {
  const normalized = ticker.trim().toUpperCase();
  if (!normalized) return null;

  const supabase = getSupabase();
  const [companyRes, psRes] = await Promise.all([
    supabase.from("companies").select("*").eq("ticker", normalized).maybeSingle(),
    supabase.from("price_sales").select("*").eq("ticker", normalized).maybeSingle(),
  ]);

  if (companyRes.error) {
    throw new Error(`Supabase query failed: ${companyRes.error.message}`);
  }
  if (!companyRes.data) return null;

  return {
    company: companyRes.data as Company,
    price_sales: (psRes.data as PriceSales | null) ?? null,
  };
}

export async function searchEquities(
  query: string,
  limit = 25,
): Promise<Partial<Company>[]> {
  const q = query.trim();
  if (!q) return [];

  const supabase = getSupabase();
  const { data, error } = await supabase
    .from("companies")
    .select(SCREENER_COLUMNS)
    .or(`ticker.ilike.%${q}%,company_name.ilike.%${q}%`)
    .order("sort_order", { ascending: true, nullsFirst: false })
    .limit(Math.min(Math.max(limit, 1), 100));

  if (error) {
    throw new Error(`Supabase query failed: ${error.message}`);
  }
  return (data ?? []) as unknown as Partial<Company>[];
}
