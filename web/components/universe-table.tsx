"use client";

import { useMemo, useState } from "react";

/**
 * Sortable / filterable table view of a universe snapshot.
 *
 * Receives the snapshot's `tickers` array verbatim and renders a fixed
 * ~17-column table covering the decision-relevant fields. Users can:
 *   - search by ticker symbol or company name
 *   - sort ascending/descending by any header
 *   - expand the truncated short_outlook column
 *
 * Heavier customisation (column visibility toggles, group filters) is
 * deferred — the JSON download is the escape hatch for power users who
 * want fields beyond the default set.
 */

type TickerRow = Record<string, unknown> & { ticker?: string };

type SortDir = "asc" | "desc";

interface Column {
  key: string;                     // dot path into the row, e.g. "fundamentals.current.rating"
  label: string;
  numeric?: boolean;
  align?: "left" | "right";
  format?: (v: unknown) => string;
  truncate?: number;               // max chars to show inline
}

const get = (row: TickerRow, path: string): unknown => {
  const parts = path.split(".");
  let cur: unknown = row;
  for (const p of parts) {
    if (cur && typeof cur === "object" && p in (cur as Record<string, unknown>)) {
      cur = (cur as Record<string, unknown>)[p];
    } else {
      return null;
    }
  }
  return cur;
};

const fmtPct = (v: unknown) => {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return `${n > 0 ? "+" : ""}${n.toFixed(1)}%`;
};

const fmtNum = (decimals: number) => (v: unknown) => {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return n.toFixed(decimals);
};

const fmtUsd = (v: unknown) => {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return `$${n.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
};

const fmtFraction1Pct = (v: unknown) => {
  // Stored as 0.34 → "+34%"
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return `${n > 0 ? "+" : ""}${(n * 100).toFixed(0)}%`;
};

const COLUMNS: Column[] = [
  { key: "ticker", label: "Ticker", align: "left" },
  { key: "sector", label: "Sector", align: "left", truncate: 18 },
  { key: "fundamentals.current.rating", label: "Rating", numeric: true, align: "right", format: fmtNum(2) },
  { key: "momentum.composite_score", label: "Score", numeric: true, align: "right", format: fmtNum(2) },
  { key: "fundamentals.current.r40_score", label: "R40", numeric: true, align: "right", format: fmtNum(0) },
  { key: "fundamentals.current.rev_growth_ttm_pct", label: "Rev TTM", numeric: true, align: "right", format: fmtPct },
  { key: "fundamentals.current.rev_growth_qoq_pct", label: "Rev QoQ", numeric: true, align: "right", format: fmtPct },
  { key: "fundamentals.current.gross_margin_pct", label: "GM%", numeric: true, align: "right", format: fmtPct },
  { key: "fundamentals.current.operating_margin_pct", label: "Op Mgn", numeric: true, align: "right", format: fmtPct },
  { key: "fundamentals.current.fcf_margin_pct", label: "FCF Mgn", numeric: true, align: "right", format: fmtPct },
  { key: "valuation.price", label: "Price", numeric: true, align: "right", format: fmtUsd },
  { key: "valuation.ps_now", label: "P/S", numeric: true, align: "right", format: fmtNum(1) },
  { key: "valuation.ps_median_12m", label: "P/S 12m med", numeric: true, align: "right", format: fmtNum(1) },
  { key: "momentum.perf_52w_vs_spy", label: "vs SPY 52w", numeric: true, align: "right", format: fmtFraction1Pct },
  { key: "valuation.price_pct_of_52w_high", label: "% of 52w hi", numeric: true, align: "right", format: fmtFraction1Pct },
  { key: "status", label: "Status", align: "left" },
  { key: "narrative.short_outlook", label: "Outlook", align: "left", truncate: 80 },
];

const numericValue = (v: unknown): number => {
  if (v === null || v === undefined || v === "") return Number.NEGATIVE_INFINITY;
  const n = Number(v);
  return Number.isFinite(n) ? n : Number.NEGATIVE_INFINITY;
};

const stringValue = (v: unknown): string =>
  v === null || v === undefined ? "" : String(v).toLowerCase();

const truncate = (s: string, max: number): string =>
  s.length <= max ? s : `${s.slice(0, max - 1)}…`;

export default function UniverseTable({ tickers }: { tickers: TickerRow[] }) {
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<string>("momentum.composite_score");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    const col = COLUMNS.find((c) => c.key === sortKey);
    const numeric = col?.numeric ?? false;
    const filtered = q
      ? tickers.filter((r) => {
          const t = String(r.ticker ?? "").toLowerCase();
          const c = String(r.company_name ?? "").toLowerCase();
          return t.includes(q) || c.includes(q);
        })
      : tickers;
    const sorted = [...filtered].sort((a, b) => {
      if (numeric) {
        return numericValue(get(a, sortKey)) - numericValue(get(b, sortKey));
      }
      return stringValue(get(a, sortKey)).localeCompare(
        stringValue(get(b, sortKey)),
      );
    });
    return sortDir === "desc" ? sorted.reverse() : sorted;
  }, [tickers, search, sortKey, sortDir]);

  const onHeaderClick = (key: string) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  return (
    <>
      <div className="flex items-center justify-between mb-3 gap-3">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filter by ticker or name…"
          className="font-mono text-sm bg-bg-hover border border-border rounded px-3 py-2 w-64 focus:outline-none focus:border-green text-text"
        />
        <p className="text-xs text-text-muted font-mono">
          Showing {rows.length} of {tickers.length}
        </p>
      </div>
      <div className="glass-card rounded-lg overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full font-mono text-xs">
            <thead className="bg-bg-hover border-b border-border text-text-dim">
              <tr>
                {COLUMNS.map((c) => {
                  const isSorted = c.key === sortKey;
                  const arrow = !isSorted ? "" : sortDir === "desc" ? " ▾" : " ▴";
                  return (
                    <th
                      key={c.key}
                      onClick={() => onHeaderClick(c.key)}
                      className={`px-3 py-2 font-normal uppercase tracking-wider cursor-pointer hover:text-text whitespace-nowrap ${
                        c.align === "right" ? "text-right" : "text-left"
                      } ${isSorted ? "text-text" : ""}`}
                    >
                      {c.label}
                      {arrow}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr
                  key={String(r.ticker ?? i)}
                  className="border-b border-border/40 hover:bg-bg-hover/40 transition-colors"
                >
                  {COLUMNS.map((c) => {
                    const raw = get(r, c.key);
                    const display = c.format
                      ? c.format(raw)
                      : raw === null || raw === undefined
                        ? "—"
                        : c.truncate
                          ? truncate(String(raw), c.truncate)
                          : String(raw);
                    const title =
                      c.truncate && raw && String(raw).length > c.truncate
                        ? String(raw)
                        : undefined;
                    return (
                      <td
                        key={c.key}
                        title={title}
                        className={`px-3 py-2 ${
                          c.align === "right"
                            ? "text-right text-text"
                            : "text-left text-text"
                        } ${c.key === "ticker" ? "font-bold text-green" : ""}`}
                      >
                        {display}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
