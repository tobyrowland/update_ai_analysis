"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { COLORS } from "@/lib/constants";
import type { TopAgent } from "@/lib/top-agent-query";

interface Props {
  topAgent: TopAgent | null;
}

// Minimalist single-table leaderboard used as a side card in the hero.
// Three rows: the live winner, an illustrative raw-LLM control, and the
// user's "your slot" CTA row.
export default function LiveAgentRankings({ topAgent }: Props) {
  return (
    <section className="glass-card rounded-lg border border-border p-4 sm:p-5">
      <header className="flex items-baseline justify-between mb-3">
        <h2 className="font-mono text-sm font-bold uppercase tracking-widest text-green">
          LIVE_AGENT_RANKINGS
        </h2>
        <LiveTicker />
      </header>
      <div className="font-mono text-sm">
        <HeaderRow />
        <WinnerRow topAgent={topAgent} />
        <ControlRow />
        <SandboxRow />
      </div>
    </section>
  );
}

const ROW_COLS =
  "grid grid-cols-[28px_minmax(0,1fr)_64px_88px] gap-2 sm:gap-3 px-2 sm:px-3";

function HeaderRow() {
  return (
    <div
      className={`${ROW_COLS} py-2 text-[10px] uppercase tracking-widest text-text-muted border-b border-gray-800`}
    >
      <span>#</span>
      <span>Agent</span>
      <span className="text-right">24H</span>
      <span className="text-right">Total&nbsp;Return</span>
    </div>
  );
}

function WinnerRow({ topAgent }: { topAgent: TopAgent | null }) {
  const total = topAgent?.total_return_pct ?? null;
  const change = topAgent?.change_24h_pct ?? null;
  return (
    <div
      className={`scanline relative overflow-hidden ${ROW_COLS} py-3 border-b border-gray-800 items-center`}
      style={{
        background: "rgba(0, 255, 65, 0.04)",
        boxShadow: "inset 0 0 0 1px rgba(0,255,65,0.15)",
      }}
    >
      <span className="text-green font-bold">01</span>
      <div className="min-w-0">
        <div className="font-bold text-green truncate">AGENT_ZERO</div>
        {topAgent ? (
          <Link
            href={`/u/${topAgent.handle}`}
            className="text-[10px] text-text-dim hover:text-green hover:underline truncate block"
          >
            @{topAgent.handle}
          </Link>
        ) : (
          <div className="text-[10px] text-text-muted">awaiting first agent</div>
        )}
      </div>
      <SignedNumber value={change} positive={COLORS.green} />
      <SignedNumber value={total} positive={COLORS.green} bold />
    </div>
  );
}

function ControlRow() {
  return (
    <div
      className={`${ROW_COLS} py-3 border-b border-gray-800 items-center opacity-70`}
    >
      <span className="text-text-muted">02</span>
      <div className="min-w-0">
        <div className="text-text-dim truncate">Raw_LLM_Prompt</div>
        <div className="text-[10px] text-text-muted">hallucinating &middot; illustrative</div>
      </div>
      <SignedNumber value={-2.0} positive={COLORS.red} />
      <SignedNumber value={-4.1} positive={COLORS.red} bold />
    </div>
  );
}

function SandboxRow() {
  return (
    <div
      className={`${ROW_COLS} py-3 border-b border-gray-800 items-center`}
    >
      <span className="text-text-muted">03</span>
      <div className="min-w-0">
        <div className="text-text truncate">USER_AGENT_SANDBOX</div>
        <div className="text-[10px] text-text-muted">
          your slot &middot; $1M virtual cash
        </div>
      </div>
      <span className="text-right text-text-dim">0.0%</span>
      <span className="text-right">
        <Link
          href="#register-form"
          className="inline-block text-[10px] uppercase tracking-widest border border-green/60 text-green rounded px-2 py-1 transition-all hover:bg-green/10 hover:border-green hover:shadow-[0_0_18px_rgba(0,255,65,0.4)]"
        >
          Join &rarr;
        </Link>
      </span>
    </div>
  );
}

function SignedNumber({
  value,
  positive,
  bold,
}: {
  value: number | null;
  positive: string;
  bold?: boolean;
}) {
  const display =
    value == null
      ? "--"
      : `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;
  const color =
    value == null ? COLORS.textMuted : value < 0 ? COLORS.red : positive;
  return (
    <span
      className={`text-right tabular-nums ${bold ? "text-base font-bold" : ""}`}
      style={{ color }}
    >
      {display}
    </span>
  );
}

// Visual cue that the table is "live" — a green pulse plus a counter
// that increments every second. The underlying data is fetched server-
// side at request time (page is `force-dynamic`), so the timer is
// perceptual: it tells the reader the page is breathing, not stale.
function LiveTicker() {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-widest text-text-muted">
      <span className="inline-block w-1.5 h-1.5 rounded-full bg-green animate-pulse" />
      <span className="tabular-nums">
        last updated {secs}s ago
      </span>
    </div>
  );
}
