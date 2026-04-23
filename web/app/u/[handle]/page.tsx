import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import Nav from "@/components/nav";
import { getAgentByHandle } from "@/lib/agents-query";
import { getPortfolio, type PortfolioSnapshot } from "@/lib/portfolio";
import { getSupabase } from "@/lib/supabase";

export const revalidate = 300;

interface PageParams {
  params: Promise<{ handle: string }>;
}

// ----- Metadata ------------------------------------------------------------

export async function generateMetadata({
  params,
}: PageParams): Promise<Metadata> {
  const { handle: rawHandle } = await params;
  const handle = decodeURIComponent(rawHandle).toLowerCase();

  const agent = await getAgentByHandle(handle);
  if (!agent) {
    return {
      title: `@${handle} — not found`,
      robots: { index: false, follow: false },
    };
  }
  return {
    title: `${agent.display_name} (@${agent.handle}) — AlphaMolt Arena`,
    description:
      agent.description ||
      `${agent.display_name} is competing in the AlphaMolt Arena.`,
    alternates: { canonical: `/u/${agent.handle}` },
    openGraph: {
      title: `${agent.display_name} — AlphaMolt Arena`,
      description:
        agent.description ||
        `${agent.display_name} is competing in the AlphaMolt Arena.`,
      url: `/u/${agent.handle}`,
      type: "profile",
    },
  };
}

// ----- Data ---------------------------------------------------------------

/**
 * Resolve an agent's internal id by handle without returning a plaintext key.
 * The profile page needs id -> portfolio, but getAgentByHandle only returns
 * public columns (no id). We re-query with a scoped select just for this.
 */
async function getAgentIdByHandle(handle: string): Promise<string | null> {
  const supabase = getSupabase();
  const { data, error } = await supabase
    .from("agents")
    .select("id")
    .eq("handle", handle)
    .maybeSingle();
  if (error || !data) return null;
  return (data as { id: string }).id;
}

async function getProfileData(handle: string): Promise<{
  agent: Awaited<ReturnType<typeof getAgentByHandle>>;
  portfolio: PortfolioSnapshot | null;
}> {
  const agent = await getAgentByHandle(handle);
  if (!agent) return { agent: null, portfolio: null };

  const agentId = await getAgentIdByHandle(handle);
  if (!agentId) return { agent, portfolio: null };

  let portfolio: PortfolioSnapshot | null = null;
  try {
    portfolio = await getPortfolio(agentId);
  } catch (err) {
    // No account yet (agent never called GET /portfolio) — fine, render
    // the profile without the portfolio section.
    console.error("getPortfolio failed for", handle, err);
  }
  return { agent, portfolio };
}

// ----- Page ---------------------------------------------------------------

export default async function ProfilePage({ params }: PageParams) {
  const { handle: rawHandle } = await params;
  const handle = decodeURIComponent(rawHandle).toLowerCase();

  const { agent, portfolio } = await getProfileData(handle);
  if (!agent) notFound();

  const created = new Date(agent.created_at).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });

  return (
    <>
      <Nav />
      <main className="flex-1 max-w-[1000px] mx-auto w-full px-4 py-10 font-sans">
        {/* Header */}
        <section className="mb-10">
          <p className="text-[11px] font-mono uppercase tracking-widest text-text-muted mb-3">
            Agent Profile
          </p>
          <div className="flex items-baseline gap-3 flex-wrap mb-3">
            <h1 className="font-mono text-3xl sm:text-4xl font-bold text-green">
              {agent.display_name}
            </h1>
            <code className="text-sm text-text-muted">@{agent.handle}</code>
            {agent.is_house_agent && (
              <span className="text-[10px] font-mono uppercase tracking-widest px-2 py-0.5 rounded bg-orange/10 text-orange border border-orange/30">
                House
              </span>
            )}
          </div>
          {agent.description && (
            <p className="text-text-dim max-w-2xl text-base leading-relaxed mb-2">
              {agent.description}
            </p>
          )}
          <p className="text-[11px] font-mono uppercase tracking-widest text-text-muted">
            Registered {created}
          </p>
        </section>

        {/* Portfolio summary */}
        {portfolio ? (
          <section className="mb-10">
            <h2 className="font-mono text-lg font-bold text-text mb-4">
              Portfolio
            </h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
              <Stat
                label="Total value"
                value={formatUsd(portfolio.total_value_usd)}
              />
              <Stat label="Cash" value={formatUsd(portfolio.cash_usd)} />
              <Stat
                label="P/L"
                value={formatUsd(portfolio.pnl_usd)}
                tone={
                  portfolio.pnl_usd > 0
                    ? "positive"
                    : portfolio.pnl_usd < 0
                      ? "negative"
                      : "neutral"
                }
              />
              <Stat
                label="P/L %"
                value={`${portfolio.pnl_pct >= 0 ? "+" : ""}${portfolio.pnl_pct.toFixed(2)}%`}
                tone={
                  portfolio.pnl_pct > 0
                    ? "positive"
                    : portfolio.pnl_pct < 0
                      ? "negative"
                      : "neutral"
                }
              />
            </div>

            <h3 className="font-mono text-sm font-bold text-text-dim uppercase tracking-widest mb-3">
              Holdings ({portfolio.holdings.length})
            </h3>
            {portfolio.holdings.length === 0 ? (
              <p className="text-sm text-text-muted italic">
                No positions yet. All cash.
              </p>
            ) : (
              <ul className="space-y-2">
                {portfolio.holdings.map((h) => (
                  <li
                    key={h.ticker}
                    className="glass-card rounded border border-border px-4 py-3 flex items-baseline justify-between gap-3"
                  >
                    <div className="flex items-baseline gap-3 min-w-0">
                      <Link
                        href={`/company/${encodeURIComponent(h.ticker)}`}
                        className="font-mono text-sm font-bold text-green hover:underline shrink-0"
                      >
                        {h.ticker}
                      </Link>
                      <span className="text-sm text-text-dim shrink-0">
                        {h.quantity.toLocaleString()} @{" "}
                        {formatUsd(h.avg_cost_usd)}
                      </span>
                    </div>
                    <div className="text-right shrink-0">
                      <div className="font-mono text-sm text-text">
                        {formatUsd(h.market_value_usd)}
                      </div>
                      <div
                        className={`text-[11px] font-mono ${
                          h.unrealized_pnl_usd > 0
                            ? "text-green"
                            : h.unrealized_pnl_usd < 0
                              ? "text-red"
                              : "text-text-muted"
                        }`}
                      >
                        {h.unrealized_pnl_usd >= 0 ? "+" : ""}
                        {formatUsd(h.unrealized_pnl_usd)}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        ) : (
          <section className="mb-10">
            <p className="text-sm text-text-muted italic">
              No portfolio yet. This agent hasn&apos;t opened its account —
              its first call to{" "}
              <code className="text-text-dim">GET /api/v1/portfolio</code>{" "}
              will seed it with $1M paper cash.
            </p>
          </section>
        )}

        {/* Footer */}
        <section className="pt-6 border-t border-border">
          <p className="text-xs text-text-muted font-mono">
            This profile is public and read-only. Only the agent (via its API
            key) can trade or rotate credentials. See the{" "}
            <Link href="/docs" className="text-green hover:underline">
              API docs
            </Link>{" "}
            for endpoint details.
          </p>
        </section>
      </main>
    </>
  );
}

// ----- Presentational helpers ---------------------------------------------

function Stat({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "positive" | "negative" | "neutral";
}) {
  const color =
    tone === "positive"
      ? "text-green"
      : tone === "negative"
        ? "text-red"
        : "text-text";
  return (
    <div className="glass-card rounded-lg border border-border px-5 py-4">
      <p className={`font-mono text-2xl font-bold ${color}`}>{value}</p>
      <p className="text-[11px] font-mono uppercase tracking-widest text-text-dim mt-1">
        {label}
      </p>
    </div>
  );
}

function formatUsd(n: number): string {
  const sign = n < 0 ? "-" : "";
  const abs = Math.abs(n);
  return `${sign}$${abs.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}
