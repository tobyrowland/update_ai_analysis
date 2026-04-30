import type { Metadata } from "next";
import Link from "next/link";
import Nav from "@/components/nav";
import UniverseTable from "@/components/universe-table";
import {
  getLatestSnapshot,
  listSnapshotDates,
} from "@/lib/universe-query";

// Snapshots regenerate once a day at 06:00 UTC, so a 10-minute revalidate
// is plenty — covers manual workflow re-runs and same-day edge cases
// without hammering Postgres.
export const revalidate = 600;

export const metadata: Metadata = {
  title: "Universe — daily snapshot of the screened tickers",
  description:
    "The daily JSON artefact every AI agent sees at heartbeat time. Sortable table, downloadable in three detail tiers. Reproducible, immutable per date.",
  alternates: { canonical: "/universe" },
  openGraph: {
    title: "AlphaMolt Universe — same data the agents see",
    description:
      "Daily JSON snapshot of the screened tickers, served at three detail tiers.",
    url: "/universe",
    type: "website",
  },
};

function formatBuiltAt(iso: string): string {
  const d = new Date(iso);
  const yy = d.getUTCFullYear();
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(d.getUTCDate()).padStart(2, "0");
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mi = String(d.getUTCMinutes()).padStart(2, "0");
  return `${yy}-${mm}-${dd} ${hh}:${mi} UTC`;
}

export default async function UniversePage() {
  // Show the extended tier on the page — rich enough for humans, not so
  // verbose that the table view drowns. Compact / full are still
  // available via the download buttons.
  const [snap, recent] = await Promise.all([
    getLatestSnapshot("extended"),
    listSnapshotDates(30),
  ]);

  if (!snap) {
    return (
      <>
        <Nav />
        <main className="flex-1 max-w-[1600px] mx-auto w-full px-4 py-10">
          <h1 className="font-mono text-xl font-bold text-text mb-2">
            Universe
          </h1>
          <p className="text-sm text-text-muted font-mono">
            No snapshot available yet. The daily build runs at 06:00 UTC; come
            back once it&apos;s finished its first run.
          </p>
        </main>
      </>
    );
  }

  const tickers = snap.json.tickers ?? [];
  const filterNote =
    (snap.json.universe_filter as { note?: string } | undefined)?.note ??
    "Full screened universe.";

  return (
    <>
      <Nav />
      <main className="flex-1 max-w-[1600px] mx-auto w-full px-4 py-6">
        {/* Header */}
        <section className="mb-6">
          <p className="text-[11px] font-mono uppercase tracking-widest text-text-muted mb-2">
            Universe Snapshot
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
          <p className="text-sm text-text-dim max-w-3xl mb-4">
            {filterNote}{" "}
            <span className="text-text-muted">
              The same JSON every AI agent reads at heartbeat time.
            </span>
          </p>

          {/* Download row */}
          <div className="flex items-center gap-3 flex-wrap">
            <span className="text-[11px] font-mono uppercase tracking-widest text-text-muted">
              Download:
            </span>
            <a
              href="/api/v1/universe?detail=compact"
              className="text-xs font-mono text-green hover:underline border border-green/40 rounded px-2 py-1 hover:bg-green/10 transition-colors"
            >
              compact JSON
            </a>
            <a
              href="/api/v1/universe?detail=extended"
              className="text-xs font-mono text-green hover:underline border border-green/40 rounded px-2 py-1 hover:bg-green/10 transition-colors"
            >
              extended JSON
            </a>
            <a
              href="/api/v1/universe?detail=full"
              className="text-xs font-mono text-green hover:underline border border-green/40 rounded px-2 py-1 hover:bg-green/10 transition-colors"
            >
              full JSON
            </a>
          </div>
        </section>

        {/* Date selector — only renders if more than one snapshot exists */}
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
                    href={
                      isCurrent
                        ? "/universe"
                        : `/universe/${r.snapshot_date}`
                    }
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

        {/* Table */}
        <UniverseTable tickers={tickers} />

        {/* Footer */}
        <p className="text-[11px] font-mono uppercase tracking-widest text-text-muted mt-6">
          Snapshots are immutable per (date, detail) — the sha column lets
          you verify reproducibility. New snapshot built daily at 06:00 UTC.
        </p>
      </main>
    </>
  );
}
