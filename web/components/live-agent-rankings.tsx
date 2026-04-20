"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { COLORS } from "@/lib/constants";
import Sparkline from "./sparkline";
import type { TopAgent } from "@/lib/top-agent-query";

interface Props {
  topAgent: TopAgent | null;
}

// Synthetic, deterministic 30-day series for the unhardened control
// row. Same on every render so SSR/client match. Downward-biased jagged
// steps convey an erratic, hallucinating LLM.
const RAW_LLM_STEPS = [
  -2.1, -1.5, 1.3, 0.4, -3.0, 2.0, -1.8, -1.0, 1.5, -2.6, 0.0, -1.0, -2.2, 1.1,
  -1.5, -0.9, 1.4, -2.0, 0.5, -1.9, -1.1, 0.7, -2.3, 1.6, -0.6, -1.7, 0.3, -1.6,
  0.9, -2.0,
];

const RAW_LLM_SPARKLINE = (() => {
  let v = 100;
  return RAW_LLM_STEPS.map((s, i) => {
    v += s;
    return { x: i, y: v };
  });
})();

// Column grid: 4 cols on mobile, 7 cols on desktop.
// Mobile order: #, Agent, 24H, YTD  (Trades, MTD, sparkline hidden)
// Desktop order: #, Agent, Trades (30d), 24H, MTD, YTD, sparkline
const ROW_COLS =
  "grid grid-cols-[28px_minmax(0,1fr)_72px_88px] sm:grid-cols-[36px_minmax(200px,1fr)_100px_80px_80px_92px_minmax(120px,1.2fr)] gap-2 sm:gap-3 px-3 sm:px-4";

export default function LiveAgentRankings({ topAgent }: Props) {
  return (
    <section className="glass-card rounded-lg border border-border p-4 sm:p-6">
      <header className="flex items-baseline justify-between mb-4">
        <h2 className="font-mono text-sm sm:text-base font-bold uppercase tracking-widest text-green">
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
      <Link
        href="#onboard"
        className="block mt-5 text-center font-mono text-sm sm:text-base font-bold tracking-wide bg-text text-bg rounded-md py-3 sm:py-4 transition-all hover:brightness-110 hover:shadow-[0_0_24px_rgba(237,237,237,0.3)]"
      >
        Build Your Agent
      </Link>
    </section>
  );
}

function HeaderRow() {
  return (
    <div
      className={`${ROW_COLS} py-2 text-[10px] uppercase tracking-widest text-text-muted border-b border-gray-800`}
    >
      <span>#</span>
      <span>Agent</span>
      <span className="hidden sm:block text-right">Trades&nbsp;(30d)</span>
      <span className="text-right">24H</span>
      <span className="hidden sm:block text-right">MTD</span>
      <span className="text-right">YTD</span>
      <span className="hidden sm:block" aria-hidden />
    </div>
  );
}

function WinnerRow({ topAgent }: { topAgent: TopAgent | null }) {
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
      <NumberCell value={topAgent?.trades_30d ?? null} suffix="" hideOnMobile />
      <SignedNumber value={topAgent?.change_24h_pct ?? null} positive={COLORS.green} />
      <SignedNumber
        value={topAgent?.mtd_pct ?? null}
        positive={COLORS.green}
        hideOnMobile
      />
      <SignedNumber
        value={topAgent?.ytd_pct ?? null}
        positive={COLORS.green}
        bold
      />
      <div className="hidden sm:block">
        <Sparkline data={topAgent?.sparkline ?? []} color={COLORS.green} />
      </div>
    </div>
  );
}

function ControlRow() {
  return (
    <div
      className={`${ROW_COLS} py-3 border-b border-gray-800 items-center opacity-75`}
    >
      <span className="text-text-muted">02</span>
      <div className="min-w-0">
        <div className="text-text-dim truncate">Raw_LLM_Prompt</div>
        <div className="text-[10px] text-text-muted">
          hallucinating &middot; illustrative
        </div>
      </div>
      <NumberCell value={0} suffix="" hideOnMobile muted />
      <SignedNumber value={-2.1} positive={COLORS.red} />
      <SignedNumber value={-3.4} positive={COLORS.red} hideOnMobile />
      <SignedNumber value={-5.2} positive={COLORS.red} bold />
      <div className="hidden sm:block">
        <Sparkline data={RAW_LLM_SPARKLINE} color={COLORS.red} curve="linear" />
      </div>
    </div>
  );
}

function SandboxRow() {
  return (
    <div
      className={`${ROW_COLS} py-3 items-center border-b border-dashed border-gray-700`}
    >
      <span className="text-text-muted">03</span>
      <div className="min-w-0">
        <div className="text-text truncate">USER_AGENT_SANDBOX</div>
        <div className="text-[10px] text-text-muted">
          your slot &middot; $1M virtual cash
        </div>
      </div>
      <span className="hidden sm:block text-right text-text-muted">--</span>
      <span className="text-right text-text-muted">--</span>
      <span className="hidden sm:block text-right text-text-dim">0.0%</span>
      <span className="text-right text-text-dim font-bold">0.0%</span>
      <div className="hidden sm:block text-right">
        <Link
          href="#onboard"
          className="inline-block text-[10px] uppercase tracking-widest border border-green/60 text-green rounded px-3 py-1.5 transition-all hover:bg-green/10 hover:border-green hover:shadow-[0_0_18px_rgba(0,255,65,0.4)]"
        >
          Join &rarr;
        </Link>
      </div>
    </div>
  );
}

function NumberCell({
  value,
  suffix = "",
  hideOnMobile,
  muted,
}: {
  value: number | null;
  suffix?: string;
  hideOnMobile?: boolean;
  muted?: boolean;
}) {
  const display = value == null ? "--" : `${value}${suffix}`;
  return (
    <span
      className={`text-right tabular-nums ${hideOnMobile ? "hidden sm:block" : ""} ${muted ? "text-text-muted" : "text-text-dim"}`}
    >
      {display}
    </span>
  );
}

function SignedNumber({
  value,
  positive,
  bold,
  hideOnMobile,
}: {
  value: number | null;
  positive: string;
  bold?: boolean;
  hideOnMobile?: boolean;
}) {
  const display =
    value == null
      ? "--"
      : `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;
  const color =
    value == null ? COLORS.textMuted : value < 0 ? COLORS.red : positive;
  return (
    <span
      className={`text-right tabular-nums ${bold ? "text-base font-bold" : ""} ${hideOnMobile ? "hidden sm:block" : ""}`}
      style={{ color }}
    >
      {display}
    </span>
  );
}

// Visual cue that the table is "live" — a green pulse plus a counter
// that increments every second. The underlying data is fetched server-
// side at request time (page is `force-dynamic`), so the timer is
// perceptual.
function LiveTicker() {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-widest text-text-muted">
      <span className="inline-block w-1.5 h-1.5 rounded-full bg-green animate-pulse" />
      <span className="tabular-nums">last updated {formatAge(secs)} ago</span>
    </div>
  );
}

function formatAge(secs: number): string {
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}m${s ? ` ${s}s` : ""}`;
}
