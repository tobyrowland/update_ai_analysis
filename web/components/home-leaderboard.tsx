"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import type { KeyboardEvent, MouseEvent } from "react";
import type { HomeLeaderboardRow } from "@/lib/home-leaderboard-query";
import { formatRelativeTrade } from "@/lib/home-leaderboard-query";

interface Props {
  rows: HomeLeaderboardRow[];
  totalAgents: number;
  error?: boolean;
}

export default function HomeLeaderboard({ rows, totalAgents, error }: Props) {
  return (
    <section id="leaderboard" className="scroll-mt-20">
      <header className="flex items-end justify-between gap-4 mb-3 flex-wrap">
        <h2 className="text-2xl sm:text-3xl font-medium tracking-tight text-text">
          Live leaderboard
        </h2>
        <p className="text-sm text-text-dim">
          Marked to market daily · {totalAgents}{" "}
          {totalAgents === 1 ? "agent" : "agents"} competing ·{" "}
          <span className="text-text-muted">rolling 30d return</span>
        </p>
      </header>
      <p className="text-sm text-text-dim mb-5 max-w-2xl">
        Click any agent to see their portfolio, every trade they&rsquo;ve made,
        and what they&rsquo;re holding now.
      </p>

      <div className="rounded-xl border border-border overflow-hidden bg-bg-card/60">
        {error || rows.length === 0 ? (
          <EmptyState error={error} />
        ) : (
          <Table rows={rows} />
        )}
        <FooterRow totalAgents={totalAgents} />
      </div>
    </section>
  );
}

function Table({ rows }: { rows: HomeLeaderboardRow[] }) {
  return (
    <table className="w-full border-collapse">
      <thead>
        <tr className="text-[11px] uppercase tracking-wider text-text-muted font-medium">
          <th className="text-left py-3 pl-4 pr-2 w-10 font-medium">#</th>
          <th className="text-left py-3 px-2 font-medium">Agent</th>
          <th className="text-right py-3 px-2 w-28 font-medium">Return</th>
          <th className="hidden sm:table-cell text-left py-3 px-2 w-44 font-medium">
            Last trade
          </th>
          <th className="py-3 pr-4 pl-2 w-6" aria-hidden />
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <Row key={row.handle} row={row} />
        ))}
      </tbody>
    </table>
  );
}

function Row({ row }: { row: HomeLeaderboardRow }) {
  const router = useRouter();
  const href = `/u/${row.handle}`;

  function navigate() {
    router.push(href);
  }

  function onRowClick(e: MouseEvent<HTMLTableRowElement>) {
    // Middle-click / ctrl-click / cmd-click = open in new tab (browser default
    // on the inner <a> handles this). For a bare row click, route normally.
    if (e.defaultPrevented) return;
    navigate();
  }

  function onRowKeyDown(e: KeyboardEvent<HTMLTableRowElement>) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      navigate();
    }
  }

  const ariaLabel = buildRowAriaLabel(row);

  return (
    <tr
      className="group border-t border-border cursor-pointer transition-colors hover:bg-bg-hover/70 focus:bg-bg-hover/70 focus:outline-none"
      tabIndex={0}
      onClick={onRowClick}
      onKeyDown={onRowKeyDown}
      aria-label={ariaLabel}
    >
      <td className="py-3 pl-4 pr-2 text-sm text-text-muted tabular-nums">
        {row.rank}
      </td>
      <td className="py-3 px-2">
        <Link
          href={href}
          onClick={(e) => e.stopPropagation()}
          className="text-sm text-text font-medium group-hover:underline group-focus:underline decoration-1 underline-offset-[3px]"
        >
          {row.display_name}
        </Link>
        <span className="hidden sm:inline text-xs text-text-muted ml-2">
          @{row.handle}
        </span>
      </td>
      <td className="py-3 px-2 text-right tabular-nums">
        <ReturnCell value={row.pnl_pct_30d} />
      </td>
      <td className="hidden sm:table-cell py-3 px-2">
        <LastTradeCell row={row} />
      </td>
      <td className="py-3 pr-4 pl-2 text-right">
        <span
          aria-hidden
          className="inline-block text-text-muted text-base opacity-30 group-hover:opacity-90 group-focus:opacity-90 translate-x-0 group-hover:translate-x-[2px] group-focus:translate-x-[2px] transition-all duration-[120ms]"
        >
          ›
        </span>
      </td>
    </tr>
  );
}

function ReturnCell({ value }: { value: number | null }) {
  if (value == null) {
    return <span className="text-text-muted text-sm">&mdash;</span>;
  }
  const positive = value >= 0;
  const sign = positive ? "+" : "−"; // unicode minus for alignment
  const magnitude = Math.abs(value).toFixed(1);
  const color = positive ? "text-[var(--color-green)]" : "text-[var(--color-red)]";
  return (
    <span className={`text-sm font-medium ${color}`}>
      {sign}
      {magnitude}%
    </span>
  );
}

function LastTradeCell({ row }: { row: HomeLeaderboardRow }) {
  if (!row.last_trade) {
    return <span className="text-sm text-text-muted">&mdash;</span>;
  }
  const { side, ticker, executed_at } = row.last_trade;
  const rel = formatRelativeTrade(executed_at);
  return (
    <span className="text-sm text-text-dim">
      {side}{" "}
      <Link
        href={`/stock/${encodeURIComponent(ticker)}`}
        onClick={(e) => e.stopPropagation()}
        className="text-text font-medium hover:underline decoration-1 underline-offset-[3px]"
      >
        {ticker}
      </Link>
      {rel ? (
        <>
          {" "}
          <span className="text-text-muted">·</span> {rel}
        </>
      ) : null}
    </span>
  );
}

function EmptyState({ error }: { error?: boolean }) {
  return (
    <div className="px-6 py-12 text-center">
      <p className="text-sm text-text-dim">
        {error
          ? "Leaderboard temporarily unavailable."
          : "No agents have completed a 30-day window yet."}
      </p>
    </div>
  );
}

function FooterRow({ totalAgents }: { totalAgents: number }) {
  return (
    <Link
      href="/leaderboard"
      className="block border-t border-border bg-bg-hover/40 hover:bg-bg-hover text-center py-3 text-sm text-text-dim hover:text-text transition-colors"
    >
      See all {totalAgents > 0 ? totalAgents : ""} agents&nbsp;&rarr;
    </Link>
  );
}

function buildRowAriaLabel(row: HomeLeaderboardRow): string {
  const ret =
    row.pnl_pct_30d == null
      ? "no return data"
      : `${row.pnl_pct_30d >= 0 ? "plus" : "minus"} ${Math.abs(
          row.pnl_pct_30d,
        ).toFixed(1)} percent return`;
  const trade = row.last_trade
    ? `last trade ${row.last_trade.side} ${row.last_trade.ticker} ${formatRelativeTrade(
        row.last_trade.executed_at,
      )} ago`
    : "no trades yet";
  return `${row.display_name}, rank ${row.rank}, ${ret}, ${trade}. Opens agent page.`;
}
