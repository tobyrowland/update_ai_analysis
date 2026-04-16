import { notFound } from "next/navigation";
import Link from "next/link";
import Nav from "@/components/nav";
import { getAgentByHandle } from "@/lib/agents-query";
import { getPortfolio, type HoldingWithMtm } from "@/lib/portfolio";

export const dynamic = "force-dynamic";

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

  const portfolio = await getPortfolio(agent.id);

  const holdingsSorted = [...portfolio.holdings].sort(
    (a, b) => b.market_value_usd - a.market_value_usd,
  );

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
      </main>
    </>
  );
}
