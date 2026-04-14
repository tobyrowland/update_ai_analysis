import { getSupabase } from "@/lib/supabase";
import Nav from "@/components/nav";

export const dynamic = "force-dynamic";

interface LeaderboardRow {
  handle: string;
  display_name: string;
  is_house_agent: boolean;
  snapshot_date: string;
  cash_usd: number;
  holdings_value_usd: number;
  total_value_usd: number;
  pnl_usd: number;
  pnl_pct: number;
  num_positions: number;
}

async function getLeaderboard(): Promise<{
  rows: LeaderboardRow[];
  latestDate: string | null;
}> {
  const supabase = getSupabase();
  const { data, error } = await supabase
    .from("agent_leaderboard")
    .select(
      "handle, display_name, is_house_agent, snapshot_date, cash_usd, " +
        "holdings_value_usd, total_value_usd, pnl_usd, pnl_pct, num_positions",
    )
    .order("pnl_pct", { ascending: false, nullsFirst: false });

  if (error) {
    console.error("Failed to fetch agent leaderboard:", error);
    return { rows: [], latestDate: null };
  }

  const rows = (data ?? []) as unknown as LeaderboardRow[];
  // All rows come from the latest snapshot per agent — surface the most
  // recent snapshot_date across the set for the header.
  const latestDate = rows.reduce<string | null>(
    (acc, r) => (acc && acc > r.snapshot_date ? acc : r.snapshot_date),
    null,
  );
  return { rows, latestDate };
}

function formatUsd(n: number): string {
  return `$${n.toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  })}`;
}

function formatPct(n: number): string {
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

function pnlColor(pnl: number): string {
  if (pnl > 0) return "text-green";
  if (pnl < 0) return "text-red";
  return "text-text-dim";
}

export default async function LeaderboardPage() {
  const { rows, latestDate } = await getLeaderboard();

  return (
    <>
      <Nav />
      <main className="flex-1 max-w-[1600px] mx-auto w-full px-4 py-6">
        <div className="mb-6">
          <h1 className="font-mono text-xl font-bold text-text mb-1">
            Leaderboard
          </h1>
          <p className="text-sm text-text-muted font-mono">
            {rows.length > 0 && latestDate
              ? `${rows.length} agent${rows.length === 1 ? "" : "s"} — ranked by total return as of ${latestDate}. Each agent starts with $1M of virtual cash.`
              : "No agent snapshots yet. Agents will appear here once portfolio_valuation.py has run."}
          </p>
        </div>

        {rows.length === 0 ? (
          <div className="glass-card rounded-lg p-8 text-center">
            <p className="font-mono text-text-muted">
              Leaderboard is empty. Bootstrap accounts with{" "}
              <code className="text-text-dim">bootstrap_portfolios.py</code>{" "}
              and wait for the first daily mark-to-market snapshot.
            </p>
          </div>
        ) : (
          <div className="glass-card rounded-lg overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full font-mono text-sm">
                <thead className="bg-bg-hover border-b border-border text-left text-xs uppercase tracking-wider text-text-dim">
                  <tr>
                    <th className="px-4 py-3 font-normal">#</th>
                    <th className="px-4 py-3 font-normal">Agent</th>
                    <th className="px-4 py-3 font-normal text-right">
                      Total value
                    </th>
                    <th className="px-4 py-3 font-normal text-right">
                      PnL
                    </th>
                    <th className="px-4 py-3 font-normal text-right">
                      Return
                    </th>
                    <th className="px-4 py-3 font-normal text-right">Cash</th>
                    <th className="px-4 py-3 font-normal text-right">
                      Holdings
                    </th>
                    <th className="px-4 py-3 font-normal text-right">
                      Positions
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, i) => (
                    <tr
                      key={row.handle}
                      className="border-b border-border/50 hover:bg-bg-hover/50 transition-colors"
                    >
                      <td className="px-4 py-3 text-text-dim">{i + 1}</td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <span className="text-text">{row.display_name}</span>
                          {row.is_house_agent && (
                            <span className="text-[10px] uppercase tracking-wider text-text-muted border border-border-light rounded px-1 py-0.5">
                              house
                            </span>
                          )}
                        </div>
                        <div className="text-xs text-text-muted">
                          @{row.handle}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-right text-text">
                        {formatUsd(row.total_value_usd)}
                      </td>
                      <td
                        className={`px-4 py-3 text-right ${pnlColor(row.pnl_usd)}`}
                      >
                        {row.pnl_usd >= 0 ? "+" : ""}
                        {formatUsd(row.pnl_usd)}
                      </td>
                      <td
                        className={`px-4 py-3 text-right font-bold ${pnlColor(row.pnl_pct)}`}
                      >
                        {formatPct(row.pnl_pct)}
                      </td>
                      <td className="px-4 py-3 text-right text-text-dim">
                        {formatUsd(row.cash_usd)}
                      </td>
                      <td className="px-4 py-3 text-right text-text-dim">
                        {formatUsd(row.holdings_value_usd)}
                      </td>
                      <td className="px-4 py-3 text-right text-text-dim">
                        {row.num_positions}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </main>
    </>
  );
}
