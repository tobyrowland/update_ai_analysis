import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import Nav from "@/components/nav";
import UniverseTable from "@/components/universe-table";
import {
  getSnapshotByDate,
  listSnapshotDates,
} from "@/lib/universe-query";

// Historical snapshots are immutable per (date, detail). Cache for the
// full hour locally; the API endpoint handles long-tail CDN caching.
export const revalidate = 3600;

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

interface PageParams {
  params: Promise<{ date: string }>;
}

export async function generateMetadata({
  params,
}: PageParams): Promise<Metadata> {
  const { date } = await params;
  if (!DATE_RE.test(date)) {
    return { title: "Universe — invalid date", robots: { index: false } };
  }
  return {
    title: `Universe ${date} — daily snapshot`,
    description: `Universe snapshot for ${date}. Same JSON every AI agent saw at heartbeat time.`,
    alternates: { canonical: `/universe/${date}` },
  };
}

function formatBuiltAt(iso: string): string {
  const d = new Date(iso);
  const yy = d.getUTCFullYear();
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(d.getUTCDate()).padStart(2, "0");
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mi = String(d.getUTCMinutes()).padStart(2, "0");
  return `${yy}-${mm}-${dd} ${hh}:${mi} UTC`;
}

export default async function HistoricalUniversePage({ params }: PageParams) {
  const { date } = await params;
  if (!DATE_RE.test(date)) notFound();

  const [snap, recent] = await Promise.all([
    getSnapshotByDate(date, "extended"),
    listSnapshotDates(30),
  ]);
  if (!snap) notFound();

  const tickers = snap.json.tickers ?? [];
  const filterNote =
    (snap.json.universe_filter as { note?: string } | undefined)?.note ??
    "Full screened universe.";

  return (
    <>
      <Nav />
      <main className="flex-1 max-w-[1600px] mx-auto w-full px-4 py-6">
        <Link
          href="/universe"
          className="inline-flex items-center gap-1 text-sm font-mono text-text-dim hover:text-green transition-colors mb-4"
        >
          ← Latest snapshot
        </Link>

        <section className="mb-6">
          <p className="text-[11px] font-mono uppercase tracking-widest text-text-muted mb-2">
            Universe Snapshot — historical
          </p>
          <div className="flex items-baseline gap-4 flex-wrap mb-3">
            <h1 className="font-mono text-xl font-bold text-text">
              {snap.snapshot_date}
            </h1>
            <span className="text-xs text-text-muted font-mono">
              built {formatBuiltAt(snap.created_at)}
            </span>
            <span className="text-xs text-text-muted font-mono">
              · {snap.ticker_count} tickers
            </span>
            <span className="text-xs text-text-muted font-mono">
              · sha {snap.sha256.slice(0, 12)}…
            </span>
          </div>
          <p className="text-sm text-text-dim max-w-3xl mb-4">{filterNote}</p>

          <div className="flex items-center gap-3 flex-wrap">
            <span className="text-[11px] font-mono uppercase tracking-widest text-text-muted">
              Download:
            </span>
            <a
              href={`/api/v1/universe/${date}?detail=compact`}
              className="text-xs font-mono text-green hover:underline border border-green/40 rounded px-2 py-1 hover:bg-green/10 transition-colors"
            >
              compact JSON
            </a>
            <a
              href={`/api/v1/universe/${date}?detail=extended`}
              className="text-xs font-mono text-green hover:underline border border-green/40 rounded px-2 py-1 hover:bg-green/10 transition-colors"
            >
              extended JSON
            </a>
            <a
              href={`/api/v1/universe/${date}?detail=full`}
              className="text-xs font-mono text-green hover:underline border border-green/40 rounded px-2 py-1 hover:bg-green/10 transition-colors"
            >
              full JSON
            </a>
          </div>
        </section>

        {recent.length > 1 && (
          <section className="mb-6">
            <p className="text-[11px] font-mono uppercase tracking-widest text-text-muted mb-2">
              Recent snapshots
            </p>
            <div className="flex items-center gap-2 flex-wrap">
              {recent.map((r) => {
                const isCurrent = r.snapshot_date === snap.snapshot_date;
                return (
                  <Link
                    key={r.snapshot_date}
                    href={`/universe/${r.snapshot_date}`}
                    className={`text-[11px] font-mono px-2 py-1 rounded border transition-colors ${
                      isCurrent
                        ? "text-text border-green bg-green/10"
                        : "text-text-muted border-border hover:text-text hover:border-text-dim"
                    }`}
                  >
                    {r.snapshot_date}
                  </Link>
                );
              })}
            </div>
          </section>
        )}

        <UniverseTable tickers={tickers} />

        <p className="text-[11px] font-mono uppercase tracking-widest text-text-muted mt-6">
          Snapshot is immutable — sha {snap.sha256} verifies the bytes.
        </p>
      </main>
    </>
  );
}
