"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { setPortfolioRebalanceCadence } from "@/lib/portfolios-mutations";

/**
 * Owner control for how often the heartbeat re-evaluates the portfolio
 * (migration 051). A compact two-segment switch — Weekly (default) | Daily —
 * sitting next to the visibility pill in the page-header row.
 *
 * The heartbeat workflow runs every day, but each portfolio only acts on a
 * tick once its cadence has elapsed (agent_heartbeat._portfolio_is_due), so
 * Weekly rebalances ~once a week and Daily reconsiders every day.
 */
export default function RebalanceCadenceToggle({
  portfolioId,
  cadence,
}: {
  portfolioId: string;
  cadence: "daily" | "weekly";
}) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  function setCadence(next: "daily" | "weekly") {
    if (next === cadence || pending) return;
    setError(null);
    startTransition(async () => {
      const result = await setPortfolioRebalanceCadence({
        portfolioId,
        cadence: next,
      });
      if (!result.ok) {
        setError(result.error);
        return;
      }
      router.refresh();
    });
  }

  const options: { value: "weekly" | "daily"; label: string }[] = [
    { value: "weekly", label: "Weekly" },
    { value: "daily", label: "Daily" },
  ];

  return (
    <div className="inline-flex flex-col gap-1">
      <span
        className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.02] px-3 py-1 text-[11px] font-mono uppercase tracking-[0.14em]"
        title="How often your team re-evaluates this portfolio. Weekly rebalances about once a week; Daily reconsiders every day."
      >
        <span className="text-text-muted">Rebalance</span>
        <span aria-hidden className="text-text-muted/60">
          ·
        </span>
        <span
          role="group"
          aria-label="Rebalance cadence"
          className="inline-flex items-center gap-0.5"
        >
          {options.map((opt) => {
            const active = cadence === opt.value;
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => setCadence(opt.value)}
                disabled={pending}
                aria-pressed={active}
                className={`rounded px-1.5 py-0.5 transition-colors disabled:cursor-not-allowed focus:outline-none focus-visible:ring-2 focus-visible:ring-text/40 ${
                  active
                    ? "text-[var(--color-green)]"
                    : "text-text-muted hover:text-text disabled:opacity-50"
                }`}
              >
                {pending && !active ? "…" : opt.label}
              </button>
            );
          })}
        </span>
      </span>
      {error && (
        <span className="text-xs text-[var(--color-red)] font-mono">{error}</span>
      )}
    </div>
  );
}
