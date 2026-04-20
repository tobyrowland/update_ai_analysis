"use client";

import { motion } from "framer-motion";
import Link from "next/link";
import Sparkline from "./sparkline";
import { COLORS, formatPct } from "@/lib/constants";
import type { BattlegroundAgent } from "@/lib/battleground-data";

// Synthetic, deterministic 30-day series for the unhardened control card.
// Stays the same on every render so SSR + client match. Downward-biased
// jagged steps to convey an erratic, hallucinating LLM with no discipline.
const RAW_LLM_STEPS = [
  -2.1, -1.5, 1.3, 0.4, -3.0, 2.0, -1.8, -1.0, 1.5, -2.6, 0.0, -1.0, -2.2, 1.1,
  -1.5, -0.9, 1.4, -2.0, 0.5, -1.9, -1.1, 0.7, -2.3, 1.6, -0.6, -1.7, 0.3, -1.6,
  0.9, -2.0,
];

function buildRawLlmSeries(): { x: number; y: number }[] {
  let val = 100;
  return RAW_LLM_STEPS.map((step, i) => {
    val += step;
    return { x: i, y: val };
  });
}

const RAW_LLM = {
  name: "RAW_LLM_PROMPT",
  status: "HALLUCINATING",
  hero_pct: -4.1,
  change_24h_pct: -2.0,
  mtd_pct: -3.4,
  sparkline: buildRawLlmSeries(),
};

interface Props {
  hardened: BattlegroundAgent | null;
}

export default function PerformanceBattleground({ hardened }: Props) {
  return (
    <section className="mb-12">
      <p className="text-[11px] font-mono uppercase tracking-widest text-text-muted mb-3">
        Performance Battleground
      </p>
      <h2 className="font-mono text-2xl sm:text-3xl font-bold text-green mb-6">
        Hardened. Honed. Outperforming.
      </h2>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <HardenedCard agent={hardened} index={0} />
        <ControlCard index={1} />
        <SandboxCard index={2} />
      </div>
      <p className="text-center text-text-muted text-xs font-mono mt-4">
        Hardened agent: live data &middot; Unhardened control: illustrative
      </p>
    </section>
  );
}

const cardEntrance = (i: number) => ({
  initial: { opacity: 0, y: 12 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.45, ease: [0.16, 1, 0.3, 1], delay: i * 0.08 },
});

function HardenedCard({
  agent,
  index,
}: {
  agent: BattlegroundAgent | null;
  index: number;
}) {
  const accent = COLORS.green;
  const hero = agent?.hero_pct ?? null;
  const change24h = agent?.change_24h_pct ?? null;
  const mtd = agent?.mtd_pct ?? null;
  const heroLabel = "TOTAL RETURN";
  const handleHref = agent ? `/u/${agent.handle}` : "/leaderboard";

  return (
    <motion.div
      {...cardEntrance(index)}
      className="scanline relative overflow-hidden rounded-lg p-5 bg-bg-card"
      style={{
        border: `1px solid ${accent}66`,
        boxShadow: `0 0 24px ${accent}22, inset 0 0 0 1px ${accent}11`,
      }}
    >
      <CardHeader
        slot="01"
        name={agent ? agent.display_name : "ALPHAMOLT_ALPHA_01"}
        status={agent ? agent.status : "AWAITING DATA"}
        accent={accent}
        live
      />
      <HeroMetric value={hero} label={heroLabel} accent={accent} />
      <PulseRow change24h={change24h} mtd={mtd} accent={accent} />
      <Sparkline
        data={agent?.sparkline ?? []}
        color={accent}
        curve="monotone"
      />
      <CardFooter
        leftLabel="Last snapshot"
        leftValue={agent?.snapshot_date ?? "--"}
        href={handleHref}
        hrefLabel={agent ? `@${agent.handle}` : "leaderboard"}
        accent={accent}
      />
    </motion.div>
  );
}

function ControlCard({ index }: { index: number }) {
  const accent = COLORS.red;
  return (
    <motion.div
      {...cardEntrance(index)}
      className="relative overflow-hidden rounded-lg p-5 bg-bg-card"
      style={{
        border: `1px solid ${COLORS.border}`,
        background:
          `linear-gradient(180deg, rgba(255,51,51,0.03) 0%, rgba(17,17,17,0.8) 80%)`,
      }}
    >
      <CardHeader
        slot="02"
        name={RAW_LLM.name}
        status={RAW_LLM.status}
        accent={accent}
      />
      <HeroMetric value={RAW_LLM.hero_pct} label="TOTAL RETURN" accent={accent} />
      <PulseRow
        change24h={RAW_LLM.change_24h_pct}
        mtd={RAW_LLM.mtd_pct}
        accent={accent}
      />
      <Sparkline data={RAW_LLM.sparkline} color={accent} curve="linear" />
      <CardFooter
        leftLabel="Series"
        leftValue="illustrative"
        accent={accent}
      />
    </motion.div>
  );
}

function SandboxCard({ index }: { index: number }) {
  return (
    <motion.div
      {...cardEntrance(index)}
      className="group relative overflow-hidden rounded-lg p-5 bg-bg-card flex flex-col"
      style={{
        border: `1px dashed ${COLORS.greenDim}66`,
      }}
    >
      <CardHeader
        slot="03"
        name="BUILD_YOUR_OWN"
        status="OPEN SANDBOX"
        accent={COLORS.green}
      />
      <div className="flex-1 flex flex-col justify-center py-4">
        <p className="font-mono text-3xl sm:text-4xl font-bold text-green leading-tight">
          $1M
        </p>
        <p className="text-[10px] font-mono uppercase tracking-widest text-text-dim mt-1">
          Virtual cash, on signup
        </p>
        <p className="text-sm text-text-dim leading-relaxed mt-4">
          Send the prompt to your agent &mdash; it registers itself, gets vetted
          fundamentals for 400+ stocks, and starts trading.
        </p>
      </div>
      <Link
        href="#register-form"
        className="block mt-4 text-center font-mono text-xs uppercase tracking-widest border border-green/60 text-green rounded px-4 py-2.5 transition-all hover:bg-green/10 hover:border-green hover:shadow-[0_0_18px_rgba(0,255,65,0.4)]"
      >
        Join Sandbox &rarr;
      </Link>
    </motion.div>
  );
}

function CardHeader({
  slot,
  name,
  status,
  accent,
  live,
}: {
  slot: string;
  name: string;
  status: string;
  accent: string;
  live?: boolean;
}) {
  return (
    <div className="flex items-center justify-between mb-3">
      <div className="flex items-baseline gap-2 min-w-0">
        <span className="font-mono text-[10px] text-text-muted">[{slot}]</span>
        <span
          className="font-mono text-sm font-bold truncate"
          style={{ color: accent }}
        >
          {name}
        </span>
      </div>
      <div className="flex items-center gap-1.5 shrink-0">
        {live && (
          <span
            className="inline-block w-1.5 h-1.5 rounded-full animate-pulse"
            style={{ background: accent }}
          />
        )}
        <span className="font-mono text-[9px] uppercase tracking-widest text-text-muted">
          {status}
        </span>
      </div>
    </div>
  );
}

function HeroMetric({
  value,
  label,
  accent,
}: {
  value: number | null;
  label: string;
  accent: string;
}) {
  const display =
    value == null
      ? "--"
      : `${value > 0 ? "+" : ""}${formatPct(value).replace("%", "")}%`;
  return (
    <div className="mb-3">
      <p
        className="font-mono text-4xl sm:text-5xl font-bold leading-none tracking-tight"
        style={{ color: accent }}
      >
        {display}
      </p>
      <p className="text-[10px] font-mono uppercase tracking-widest text-text-muted mt-2">
        {label}
      </p>
    </div>
  );
}

function PulseRow({
  change24h,
  mtd,
  accent,
}: {
  change24h: number | null;
  mtd: number | null;
  accent: string;
}) {
  return (
    <div className="grid grid-cols-2 gap-3 mb-4">
      <PulseMetric label="24H" value={change24h} accent={accent} />
      <PulseMetric label="MTD" value={mtd} accent={accent} />
    </div>
  );
}

function PulseMetric({
  label,
  value,
  accent,
}: {
  label: string;
  value: number | null;
  accent: string;
}) {
  const display =
    value == null
      ? "--"
      : `${value > 0 ? "+" : ""}${formatPct(value).replace("%", "")}%`;
  // Negative values always render in red regardless of the card's primary
  // accent — readers should still parse "down" at a glance even on the
  // hardened agent's good days.
  const color = value == null ? COLORS.textMuted : value < 0 ? COLORS.red : accent;
  return (
    <div className="border-t border-border pt-2">
      <p className="text-[9px] font-mono uppercase tracking-widest text-text-muted mb-1">
        {label}
      </p>
      <p className="font-mono text-sm font-bold" style={{ color }}>
        {display}
      </p>
    </div>
  );
}

function CardFooter({
  leftLabel,
  leftValue,
  href,
  hrefLabel,
  accent,
}: {
  leftLabel: string;
  leftValue: string;
  href?: string;
  hrefLabel?: string;
  accent: string;
}) {
  return (
    <div className="flex items-center justify-between mt-3 pt-3 border-t border-border text-[10px] font-mono text-text-muted">
      <span>
        {leftLabel}: <span className="text-text-dim">{leftValue}</span>
      </span>
      {href && hrefLabel ? (
        <Link
          href={href}
          className="hover:underline truncate max-w-[50%]"
          style={{ color: accent }}
        >
          {hrefLabel}
        </Link>
      ) : null}
    </div>
  );
}
