import type { HoldingWithMtm } from "@/lib/portfolio";

/**
 * Largest sector exposure as a single compact chip. Concentration risk is the
 * whole point, so the % tints amber (≥40%) then red (≥60%) — neutral otherwise.
 * Weighted by current market value, not position count: two big names in one
 * sector matter more than ten tiny ones. Holdings with no GICS sector are
 * grouped under "Unclassified" so the math stays honest (they still count
 * toward the denominator and can surface as the headline if they dominate).
 */
export function topSectorExposure(
  holdings: HoldingWithMtm[],
): { sector: string; pct: number } | null {
  const total = holdings.reduce((s, h) => s + (h.market_value_usd ?? 0), 0);
  if (total <= 0) return null;

  const bySector = new Map<string, number>();
  for (const h of holdings) {
    const key = h.sector ?? "Unclassified";
    bySector.set(key, (bySector.get(key) ?? 0) + (h.market_value_usd ?? 0));
  }

  let top: { sector: string; value: number } | null = null;
  for (const [sector, value] of bySector) {
    if (!top || value > top.value) top = { sector, value };
  }
  if (!top) return null;
  return { sector: top.sector, pct: (top.value / total) * 100 };
}

export default function SectorChip({
  holdings,
}: {
  holdings: HoldingWithMtm[];
}) {
  const top = topSectorExposure(holdings);
  if (!top) return null;

  const pctColor =
    top.pct >= 60
      ? "var(--color-red)"
      : top.pct >= 40
        ? "var(--color-yellow)"
        : undefined;

  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border border-white/12 bg-white/[0.03] px-2.5 py-1 text-[11px] font-mono text-text-muted"
      title="Largest sector exposure, by market value of holdings"
    >
      <span className="uppercase tracking-[0.12em] text-text-dim">
        Top sector
      </span>
      <span className="text-text">{top.sector}</span>
      <span style={pctColor ? { color: pctColor } : undefined}>
        {top.pct.toFixed(0)}%
      </span>
    </span>
  );
}
