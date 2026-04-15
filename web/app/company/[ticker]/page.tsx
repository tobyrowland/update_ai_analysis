import type { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import { getSupabase } from "@/lib/supabase";
import { Company, PriceSales } from "@/lib/types";
import {
  formatPct,
  formatPrice,
  formatNumber,
  parseStatus,
  parseEval,
  extractEvalRationale,
  COLORS,
} from "@/lib/constants";
import { absoluteUrl } from "@/lib/site";
import Nav from "@/components/nav";
import PsChart from "@/components/ps-chart";

export const dynamic = "force-dynamic";

async function getData(ticker: string) {
  const supabase = getSupabase();
  const [companyRes, psRes] = await Promise.all([
    supabase.from("companies").select("*").eq("ticker", ticker).single(),
    supabase.from("price_sales").select("*").eq("ticker", ticker).single(),
  ]);

  return {
    company: companyRes.data as Company | null,
    priceSales: psRes.data as PriceSales | null,
  };
}

// SEO metadata. Next.js runs this for every request alongside the page, so
// we fetch only the 5 columns we need for title/description to keep it cheap.
// Falls back to generic "ticker not found" metadata for missing companies
// rather than crashing, since the page itself handles 404 via notFound().
export async function generateMetadata({
  params,
}: {
  params: Promise<{ ticker: string }>;
}): Promise<Metadata> {
  const { ticker: rawTicker } = await params;
  const ticker = decodeURIComponent(rawTicker);

  try {
    const supabase = getSupabase();
    const { data } = await supabase
      .from("companies")
      .select("ticker, company_name, sector, country, description, short_outlook")
      .eq("ticker", ticker)
      .single();

    if (!data) {
      return {
        title: `${ticker} — not found`,
        robots: { index: false, follow: false },
      };
    }

    const name = (data.company_name as string | null) ?? ticker;
    const sector = (data.sector as string | null) ?? "equity";
    // Prefer the short outlook (1–2 sentences, written for humans) over the
    // fundamentals description. Trim to ~155 chars for SERP width.
    const raw =
      (data.short_outlook as string | null) ??
      (data.description as string | null) ??
      `${name} (${ticker}) — ${sector} equity tracked by AlphaMolt with AI narrative, fundamentals, and P/S history.`;
    const description = raw.length > 155 ? `${raw.slice(0, 152)}...` : raw;

    const title = `${name} (${ticker}) — ${sector} stock analysis`;
    const canonical = `/company/${encodeURIComponent(ticker)}`;

    return {
      title,
      description,
      alternates: { canonical },
      openGraph: {
        title,
        description,
        url: canonical,
        type: "article",
      },
      twitter: {
        card: "summary_large_image",
        title,
        description,
      },
    };
  } catch (err) {
    console.error(`generateMetadata: failed for ${ticker}:`, err);
    return { title: `${ticker} — AlphaMolt` };
  }
}

// Breadcrumb JSON-LD helper. Rendered as a <script> inside the page body so
// Google can build rich-result breadcrumbs above the SERP snippet.
function breadcrumbJsonLd(ticker: string, name: string | null) {
  const display = name ?? ticker;
  return {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      {
        "@type": "ListItem",
        position: 1,
        name: "Home",
        item: absoluteUrl("/"),
      },
      {
        "@type": "ListItem",
        position: 2,
        name: "Screener",
        item: absoluteUrl("/screener"),
      },
      {
        "@type": "ListItem",
        position: 3,
        name: `${display} (${ticker})`,
        item: absoluteUrl(`/company/${encodeURIComponent(ticker)}`),
      },
    ],
  };
}

export default async function CompanyPage({
  params,
}: {
  params: Promise<{ ticker: string }>;
}) {
  const { ticker } = await params;
  const { company, priceSales } = await getData(decodeURIComponent(ticker));

  if (!company) notFound();

  const status = parseStatus(company.status);
  const bear = parseEval(company.bear_eval);
  const bull = parseEval(company.bull_eval);
  const bearRationale = extractEvalRationale(company.bear_eval);
  const bullRationale = extractEvalRationale(company.bull_eval);
  // Defensive: flags may be a dict OR a stringified JSON string (legacy data)
  let flags: Record<string, string> = {};
  const rawFlags = company.flags;
  if (rawFlags) {
    if (typeof rawFlags === "string") {
      try {
        flags = JSON.parse(rawFlags) || {};
      } catch {
        flags = {};
      }
    } else {
      flags = rawFlags;
    }
  }

  const breadcrumb = breadcrumbJsonLd(company.ticker, company.company_name);

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(breadcrumb) }}
      />
      <Nav />
      <main className="flex-1 max-w-[1200px] mx-auto w-full px-4 py-6">
        {/* Back link */}
        <Link
          href="/screener"
          className="text-xs font-mono text-text-muted hover:text-green mb-4 inline-block"
        >
          &larr; Back to Screener
        </Link>

        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4 mb-6">
          <div>
            <div className="flex items-center gap-3 mb-1">
              <h1 className="font-mono text-2xl font-bold text-green">
                {company.ticker}
              </h1>
              <span
                className="text-xs px-2 py-0.5 rounded font-mono"
                title={status.detail ?? status.label}
                style={{
                  color: status.color,
                  backgroundColor: status.color + "15",
                }}
              >
                {status.label}
              </span>
            </div>
            {status.detail && (
              <p className="text-xs font-mono mt-1" style={{ color: status.color }}>
                {status.detail}
              </p>
            )}
            <p className="text-text-dim text-sm">
              {company.company_name} &middot; {company.exchange} &middot;{" "}
              {company.country}
            </p>
            <p className="text-text-dim text-xs mt-1">{company.sector}</p>
            {company.description && (
              <p className="text-text-muted text-sm mt-2 max-w-xl">
                {company.description}
              </p>
            )}
          </div>
          <div className="text-right font-mono">
            <p className="text-2xl font-bold">{formatPrice(company.price)}</p>
            <p className="text-xs text-text-muted">
              Rank #{company.sort_order ?? "--"} &middot; Score{" "}
              {formatNumber(company.composite_score, { decimals: 1 })}
            </p>
          </div>
        </div>

        {/* Bento grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {/* Screening */}
          <Card title="Screening Metrics">
            <Metric label="P/S Ratio" value={formatNumber(company.ps_now, { decimals: 1 })} flag={flags.ps_now} />
            <Metric label="52w High %" value={formatPct(company.price_pct_of_52w_high ? company.price_pct_of_52w_high * 100 : null)} />
            <Metric label="52w vs SPY" value={formatPct(company.perf_52w_vs_spy ? company.perf_52w_vs_spy * 100 : null)} />
            <Metric label="Rating" value={formatNumber(company.rating, { decimals: 1 })} />
            <Metric label="R40 Score" value={company.r40_score || "--"} />
          </Card>

          {/* Revenue */}
          <Card title="Revenue">
            <Metric label="Rev Growth TTM" value={formatPct(company.rev_growth_ttm_pct)} flag={flags.rev_growth_ttm_pct} />
            <Metric label="Rev Growth QoQ" value={formatPct(company.rev_growth_qoq_pct)} />
            <Metric label="Rev CAGR 3Y" value={formatPct(company.rev_cagr_pct)} />
            <Metric label="Consistency" value={company.rev_consistency_score || "--"} />
            {company.quarterly_revenue && (
              <div className="mt-2 pt-2 border-t border-border/50">
                <p className="text-xs text-text-muted mb-1">Quarterly</p>
                <p className="text-xs text-text-dim break-words">{company.quarterly_revenue}</p>
              </div>
            )}
          </Card>

          {/* Margins */}
          <Card title="Margins">
            <Metric label="Gross Margin" value={formatPct(company.gross_margin_pct)} flag={flags.gross_margin_pct} />
            <Metric label="GM Trend" value={company.gm_trend || "--"} />
            <Metric label="Operating Margin" value={formatPct(company.operating_margin_pct)} />
            <Metric label="Net Margin" value={formatPct(company.net_margin_pct)} flag={flags.net_margin_pct} />
            <Metric label="Net Margin YoY" value={formatPct(company.net_margin_yoy_pct)} />
            <Metric label="FCF Margin" value={formatPct(company.fcf_margin_pct)} flag={flags.fcf_margin_pct} />
          </Card>

          {/* Efficiency */}
          <Card title="Efficiency">
            <Metric label="OpEx/Revenue" value={formatPct(company.opex_pct_revenue)} />
            <Metric label="S&M+R&D/Revenue" value={formatPct(company.sm_rd_pct_revenue)} />
            <Metric label="Rule of 40" value={formatNumber(company.rule_of_40, { decimals: 1 })} flag={flags.rule_of_40} />
            <Metric label="Qtrs to Profit" value={company.qrtrs_to_profitability || "--"} />
          </Card>

          {/* Earnings */}
          <Card title="Earnings">
            <Metric label="EPS" value={formatNumber(company.eps_only, { prefix: "$", decimals: 2 })} />
            <Metric label="EPS YoY" value={formatPct(company.eps_yoy_pct)} />
          </Card>

          {/* Evaluations — full width so rationale text has room */}
          <div className="md:col-span-2 lg:col-span-3">
            <Card title="Example Agent Evaluations">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Bear */}
                <div className="border-l-2 pl-3" style={{ borderColor: bear.color }}>
                  <div className="flex items-baseline gap-2 mb-2">
                    <span
                      className="font-mono text-xs uppercase tracking-wider"
                      style={{ color: COLORS.textMuted }}
                    >
                      Bear (Fundamental Sentinel)
                    </span>
                    <span
                      className="font-mono text-sm font-bold"
                      style={{ color: bear.color }}
                    >
                      {bear.label}
                    </span>
                  </div>
                  {bearRationale ? (
                    <p className="text-sm text-text-dim leading-relaxed">
                      {bearRationale}
                    </p>
                  ) : (
                    <p className="text-xs text-text-muted italic">
                      No rationale provided
                    </p>
                  )}
                </div>

                {/* Bull */}
                <div className="border-l-2 pl-3" style={{ borderColor: bull.color }}>
                  <div className="flex items-baseline gap-2 mb-2">
                    <span
                      className="font-mono text-xs uppercase tracking-wider"
                      style={{ color: COLORS.textMuted }}
                    >
                      Bull (Smash-Hit Scout)
                    </span>
                    <span
                      className="font-mono text-sm font-bold"
                      style={{ color: bull.color }}
                    >
                      {bull.label}
                    </span>
                  </div>
                  {bullRationale ? (
                    <p className="text-sm text-text-dim leading-relaxed">
                      {bullRationale}
                    </p>
                  ) : (
                    <p className="text-xs text-text-muted italic">
                      No rationale provided
                    </p>
                  )}
                </div>
              </div>

              {company.in_portfolio && (
                <div className="mt-4 pt-3 border-t border-border/50">
                  <span className="text-xs font-mono text-green">
                    ✓ Selected by example agent (portfolio rank #{company.portfolio_sort_order ?? "--"})
                  </span>
                </div>
              )}
            </Card>
          </div>

          {/* AI Narrative — full width */}
          <div className="md:col-span-2 lg:col-span-3">
            <Card title="AI Narrative">
              {company.short_outlook && (
                <div className="mb-4">
                  <p className="text-xs text-text-muted mb-1">Outlook</p>
                  <p className="text-sm text-text">{company.short_outlook}</p>
                </div>
              )}
              {company.full_outlook && (
                <div className="mb-4">
                  <p className="text-xs text-text-muted mb-1">Full Outlook</p>
                  <p className="text-sm text-text-dim leading-relaxed whitespace-pre-wrap">
                    {company.full_outlook}
                  </p>
                </div>
              )}
              {company.key_risks && (
                <div className="mb-4">
                  <p className="text-xs text-text-muted mb-1">Key Risks</p>
                  <p className="text-sm text-orange">{company.key_risks}</p>
                </div>
              )}
              {company.fundamentals_snapshot && (
                <div className="mb-4">
                  <p className="text-xs text-text-muted mb-1">Fundamentals Snapshot</p>
                  <p className="text-sm text-text-dim">{company.fundamentals_snapshot}</p>
                </div>
              )}
              {company.event_impact && (
                <div>
                  <p className="text-xs text-text-muted mb-1">Event Impact</p>
                  <p className="text-sm text-text-dim">{company.event_impact}</p>
                </div>
              )}
            </Card>
          </div>

          {/* P/S History Chart */}
          {priceSales && (
            <div className="md:col-span-2 lg:col-span-3">
              <Card title="P/S History">
                <div className="grid grid-cols-2 sm:grid-cols-5 gap-4 mb-4">
                  <Metric label="Current" value={formatNumber(priceSales.ps_now, { decimals: 1 })} />
                  <Metric label="52w High" value={formatNumber(priceSales.high_52w, { decimals: 1 })} />
                  <Metric label="52w Low" value={formatNumber(priceSales.low_52w, { decimals: 1 })} />
                  <Metric label="12m Median" value={formatNumber(priceSales.median_12m, { decimals: 1 })} />
                  <Metric label="ATH" value={formatNumber(priceSales.ath, { decimals: 1 })} />
                </div>
                {priceSales.history_json && priceSales.history_json.length > 0 && (
                  <PsChart data={priceSales.history_json} />
                )}
              </Card>
            </div>
          )}

          {/* Metadata */}
          <div className="md:col-span-2 lg:col-span-3">
            <Card title="Metadata">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                <Metric label="AI Analyzed" value={company.ai_analyzed_at || "--"} />
                <Metric label="Data Updated" value={company.data_updated_at || "--"} />
                <Metric label="Scored" value={company.scored_at || "--"} />
                <Metric label="In TV Screen" value={company.in_tv_screen ? "Yes" : "No"} />
              </div>
              {Object.keys(flags).length > 0 && (
                <div className="mt-3 pt-3 border-t border-border/50">
                  <p className="text-xs text-text-muted mb-2">Flags</p>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(flags).map(([key, severity]) => (
                      <span
                        key={key}
                        className="text-xs font-mono px-2 py-0.5 rounded"
                        style={{
                          color: severity === "red" ? "#FF3333" : "#FFD700",
                          backgroundColor:
                            severity === "red" ? "#FF333315" : "#FFD70015",
                        }}
                      >
                        {key}: {severity}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </Card>
          </div>
        </div>
      </main>
    </>
  );
}

function Card({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="glass-card rounded-lg p-4 relative overflow-hidden">
      <h2 className="text-xs font-mono uppercase tracking-wider text-text-muted mb-3">
        {title}
      </h2>
      {children}
    </div>
  );
}

function Metric({
  label,
  value,
  flag,
}: {
  label: string;
  value: string;
  flag?: string;
}) {
  const flagColor = flag === "red" ? "#FF3333" : flag === "yellow" ? "#FFD700" : undefined;

  return (
    <div className="flex justify-between items-baseline py-1">
      <span className="text-xs text-text-muted">{label}</span>
      <span
        className="font-mono text-sm"
        style={{ color: flagColor }}
      >
        {value}
      </span>
    </div>
  );
}
