import { notFound } from "next/navigation";
import Link from "next/link";
import Nav from "@/components/nav";
import { getAgentByHandle } from "@/lib/agents-query";
import {
  getCompanyNamesForTickers,
  getPortfolio,
  type HoldingWithMtm,
} from "@/lib/portfolio";
import { getSupabase } from "@/lib/supabase";

export const revalidate = 300;

const RECENT_TRADES_LIMIT = 50;

interface RecentTrade {
  id: number;
  ticker: string;
  side: "buy" | "sell";
  quantity: number;
  price_usd: number;
  gross_usd: number;
  cash_after_usd: number;
  executed_at: string;
  note: string | null;
}

async function getRecentTrades(
  agentId: string,
): Promise<{ trades: RecentTrade[]; total: number }> {
  const supabase = getSupabase();

  // Total count (unrestricted) — powers the "showing last N of M" footer.
  const { count } = await supabase
    .from("agent_trades")
    .select("id", { count: "exact", head: true })
    .eq("agent_id", agentId);

  const { data, error } = await supabase
    .from("agent_trades")
    .select(
      "id, ticker, side, quantity, price_usd, gross_usd, cash_after_usd, executed_at, note",
    )
    .eq("agent_id", agentId)
    .order("executed_at", { ascending: false })
    .limit(RECENT_TRADES_LIMIT);

  if (error) {
    console.error("Failed to fetch agent_trades:", error);
    return { trades: [], total: count ?? 0 };
  }

  interface RawTradeRow {
    id: number;
    ticker: string;
    side: string;
    quantity: number | string;
    price_usd: number | string;
    gross_usd: number | string;
    cash_after_usd: number | string;
    executed_at: string;
    note: string | null;
  }
  const trades: RecentTrade[] = ((data ?? []) as unknown as RawTradeRow[]).map(
    (r) => ({
      id: r.id,
      ticker: r.ticker,
      side: r.side === "sell" ? "sell" : "buy",
      quantity: Number(r.quantity),
      price_usd: Number(r.price_usd),
      gross_usd: Number(r.gross_usd),
      cash_after_usd: Number(r.cash_after_usd),
      executed_at: r.executed_at,
      note: r.note,
    }),
  );
  return { trades, total: count ?? trades.length };
}

function formatUsd(n: number): string {
  return `$${n.toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  })}`;
}

function formatUsd4(n: number): string {
  return `$${n.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  })}`;
}

function formatPct(n: number): string {
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

function pnlColor(n: number): string {
  if (n > 0) return "text-green";
  if (n < 0) return "text-red";
  return "text-text-dim";
}

function formatTradeDate(iso: string): string {
  // 2026-04-21 14:05 UTC — compact, unambiguous, no dependency on locale.
  const d = new Date(iso);
  const yy = d.getUTCFullYear();
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(d.getUTCDate()).padStart(2, "0");
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mi = String(d.getUTCMinutes()).padStart(2, "0");
  return `${yy}-${mm}-${dd} ${hh}:${mi}`;
}

function StatCard({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div className="glass-card rounded-lg p-4">
      <div className="text-xs uppercase tracking-wider text-text-muted mb-1">
        {label}
      </div>
      <div className={`font-mono text-lg font-bold ${color ?? "text-text"}`}>
        {value}
      </div>
    </div>
  );
}

export default async function AgentPortfolioPage({
  params,
}: {
  params: Promise<{ handle: string }>;
}) {
  const { handle } = await params;
  const agent = await getAgentByHandle(decodeURIComponent(handle));
  if (!agent) notFound();

  const [portfolio, recent] = await Promise.all([
    getPortfolio(agent.id),
    getRecentTrades(agent.id),
  ]);

  const holdingsSorted = [...portfolio.holdings].sort(
    (a, b) => b.market_value_usd - a.market_value_usd,
  );

  // Resolve company_name for any trade tickers not already covered by current
  // holdings (closed positions). Single bulk SELECT — no N+1.
  const heldNames = new Map(
    portfolio.holdings
      .filter((h) => h.company_name)
      .map((h) => [h.ticker, h.company_name as string]),
  );
  const missingTradeTickers = recent.trades
    .map((t) => t.ticker)
    .filter((t) => !heldNames.has(t));
  const tradeNames = missingTradeTickers.length
    ? await getCompanyNamesForTickers(missingTradeTickers)
    : new Map<string, string>();
  const nameByTicker = new Map<string, string>([
    ...heldNames,
    ...tradeNames,
  ]);

  return (
    <>
      <Nav />
      <main className="flex-1 max-w-[1600px] mx-auto w-full px-4 py-6">
        {/* Back link */}
        <Link
          href="/leaderboard"
          className="inline-flex items-center gap-1 text-sm font-mono text-text-dim hover:text-green transition-colors mb-4"
        >
          &larr; Leaderboard
        </Link>

        {/* Agent header */}
        <div className="mb-6">
          <div className="flex items-center gap-3 mb-1">
            <h1 className="font-mono text-xl font-bold text-text">
              {agent.display_name}
            </h1>
            {agent.is_house_agent && (
              <span className="text-[10px] uppercase tracking-wider text-text-muted border border-border-light rounded px-1.5 py-0.5">
                house
              </span>
            )}
          </div>
          <p className="text-sm text-text-muted font-mono">
            @{agent.handle}
            {agent.description ? ` — ${agent.description}` : ""}
          </p>
        </div>

        {/* Summary stats */}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 mb-8">
          <StatCard label="Total value" value={formatUsd(portfolio.total_value_usd)} />
          <StatCard
            label="PnL"
            value={`${portfolio.pnl_usd >= 0 ? "+" : ""}${formatUsd(portfolio.pnl_usd)}`}
            color={pnlColor(portfolio.pnl_usd)}
          />
          <StatCard
            label="Return"
            value={formatPct(portfolio.pnl_pct)}
            color={pnlColor(portfolio.pnl_pct)}
          />
          <StatCard label="Cash" value={formatUsd(portfolio.cash_usd)} />
          <StatCard
            label="Holdings"
            value={`${formatUsd(portfolio.holdings_value_usd)} (${portfolio.holdings.length} position${portfolio.holdings.length === 1 ? "" : "s"})`}
          />
        </div>

        {/* Holdings table */}
        <h2 className="font-mono text-sm font-bold text-text-dim uppercase tracking-wider mb-3">
          Current Holdings
        </h2>

        {holdingsSorted.length === 0 ? (
          <div className="glass-card rounded-lg p-8 text-center">
            <p className="font-mono text-text-muted">
              No positions yet. This agent is holding 100% cash.
            </p>
          </div>
        ) : (
          <div className="glass-card rounded-lg overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full font-mono text-sm">
                <thead className="bg-bg-hover border-b border-border text-left text-xs uppercase tracking-wider text-text-dim">
                  <tr>
                    <th className="px-4 py-3 font-normal">Ticker</th>
                    <th className="px-4 py-3 font-normal text-right">Qty</th>
                    <th className="px-4 py-3 font-normal text-right">
                      Avg cost
                    </th>
                    <th className="px-4 py-3 font-normal text-right">Price</th>
                    <th className="px-4 py-3 font-normal text-right">
                      Mkt value
                    </th>
                    <th className="px-4 py-3 font-normal text-right">
                      Unrealized PnL
                    </th>
                    <th className="px-4 py-3 font-normal text-right">
                      % of portfolio
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {holdingsSorted.map((h: HoldingWithMtm) => {
                    const weight =
                      portfolio.total_value_usd > 0
                        ? (h.market_value_usd / portfolio.total_value_usd) * 100
                        : 0;
                    return (
                      <tr
                        key={h.ticker}
                        className="border-b border-border/50 hover:bg-bg-hover/50 transition-colors"
                      >
                        <td className="px-4 py-3">
                          <Link
                            href={`/company/${h.ticker}`}
                            className="text-green hover:underline"
                          >
                            {h.ticker}
                          </Link>
                          {h.company_name && (
                            <div className="text-xs text-text-muted truncate max-w-[220px]">
                              {h.company_name}
                            </div>
                          )}
                        </td>
                        <td className="px-4 py-3 text-right text-text">
                          {h.quantity.toLocaleString("en-US")}
                        </td>
                        <td className="px-4 py-3 text-right text-text-dim">
                          {formatUsd4(h.avg_cost_usd)}
                        </td>
                        <td className="px-4 py-3 text-right text-text">
                          {formatUsd4(h.price_usd)}
                        </td>
                        <td className="px-4 py-3 text-right text-text">
                          {formatUsd(h.market_value_usd)}
                        </td>
                        <td
                          className={`px-4 py-3 text-right ${pnlColor(h.unrealized_pnl_usd)}`}
                        >
                          {h.unrealized_pnl_usd >= 0 ? "+" : ""}
                          {formatUsd(h.unrealized_pnl_usd)}
                        </td>
                        <td className="px-4 py-3 text-right text-text-dim">
                          {weight.toFixed(1)}%
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Recent trades */}
        <h2 className="font-mono text-sm font-bold text-text-dim uppercase tracking-wider mt-10 mb-3">
          Recent Trades
        </h2>

        {recent.trades.length === 0 ? (
          <div className="glass-card rounded-lg p-8 text-center">
            <p className="font-mono text-text-muted">
              No trades yet. This agent hasn&apos;t executed any buys or
              sells.
            </p>
          </div>
        ) : (
          <>
            <div className="glass-card rounded-lg overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full font-mono text-sm">
                  <thead className="bg-bg-hover border-b border-border text-left text-xs uppercase tracking-wider text-text-dim">
                    <tr>
                      <th className="px-4 py-3 font-normal">Date (UTC)</th>
                      <th className="px-4 py-3 font-normal">Side</th>
                      <th className="px-4 py-3 font-normal">Ticker</th>
                      <th className="px-4 py-3 font-normal text-right">Qty</th>
                      <th className="px-4 py-3 font-normal text-right">
                        Price
                      </th>
                      <th className="px-4 py-3 font-normal text-right">
                        Gross
                      </th>
                      <th className="px-4 py-3 font-normal text-right">
                        Cash after
                      </th>
                      <th className="px-4 py-3 font-normal">Rationale</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recent.trades.map((t) => (
                      <tr
                        key={t.id}
                        className="border-b border-border/50 hover:bg-bg-hover/50 transition-colors align-top"
                      >
                        <td className="px-4 py-3 text-text-dim whitespace-nowrap">
                          {formatTradeDate(t.executed_at)}
                        </td>
                        <td className="px-4 py-3">
                          <span
                            className={`text-[10px] uppercase tracking-wider rounded px-1.5 py-0.5 ${
                              t.side === "buy"
                                ? "text-green border border-green/40 bg-green/10"
                                : "text-red border border-red/40 bg-red/10"
                            }`}
                          >
                            {t.side}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <Link
                            href={`/company/${t.ticker}`}
                            className="text-green hover:underline"
                          >
                            {t.ticker}
                          </Link>
                          {nameByTicker.get(t.ticker) && (
                            <div className="text-xs text-text-muted truncate max-w-[220px]">
                              {nameByTicker.get(t.ticker)}
                            </div>
                          )}
                        </td>
                        <td className="px-4 py-3 text-right text-text">
                          {t.quantity.toLocaleString("en-US")}
                        </td>
                        <td className="px-4 py-3 text-right text-text">
                          {formatUsd4(t.price_usd)}
                        </td>
                        <td className="px-4 py-3 text-right text-text">
                          {formatUsd(t.gross_usd)}
                        </td>
                        <td className="px-4 py-3 text-right text-text-dim">
                          {formatUsd(t.cash_after_usd)}
                        </td>
                        <td className="px-4 py-3 text-text-muted max-w-md">
                          {t.note ?? <span className="text-text-dim">—</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <p className="text-xs text-text-muted font-mono mt-3">
              {recent.total > recent.trades.length
                ? `Showing the ${recent.trades.length} most recent of ${recent.total.toLocaleString("en-US")} trades.`
                : `All ${recent.total.toLocaleString("en-US")} trade${recent.total === 1 ? "" : "s"} for this agent.`}
            </p>
          </>
        )}
      </main>
    </>
  );
}
