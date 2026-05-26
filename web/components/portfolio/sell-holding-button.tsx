"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { sellHolding } from "@/lib/portfolios-mutations";

/**
 * Per-row "Sell" button on the owner's portfolio page. Click reveals an
 * inline confirmation; confirm calls the `sellHolding` server action,
 * which does the atomic full-position exit via `execute_portfolio_sell`
 * (migration 025 + 035 for the `manual` attribution agent).
 *
 * Designed to sit alongside the per-row remove control. Compact, no
 * modal — confirm in place to avoid dropping a heavyweight dialog on a
 * dense holdings table.
 */
export default function SellHoldingButton({
  ticker,
  quantity,
  marketValueUsd,
}: {
  ticker: string;
  quantity: number;
  marketValueUsd: number;
}) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function onConfirm() {
    setError(null);
    startTransition(async () => {
      const result = await sellHolding({ ticker });
      if (!result.ok) {
        setError(result.error);
        setConfirming(false);
        return;
      }
      setConfirming(false);
      router.refresh();
    });
  }

  if (!confirming) {
    return (
      <div className="flex flex-col items-end gap-1">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setConfirming(true);
          }}
          className="shrink-0 px-2.5 py-1 font-mono text-[11px] uppercase tracking-widest rounded border border-red/40 text-red hover:bg-red/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-red/40 transition-colors"
          title={`Sell all ${quantity.toLocaleString()} ${ticker} at the latest price`}
        >
          Sell
        </button>
        {error && (
          <span className="text-[10px] font-mono text-red whitespace-nowrap">
            {error}
          </span>
        )}
      </div>
    );
  }

  const valuePreview = marketValueUsd.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });

  return (
    <div
      className="flex flex-col items-end gap-1"
      onClick={(e) => e.stopPropagation()}
    >
      <span className="text-[10px] font-mono text-text-muted text-right whitespace-nowrap">
        Sell {quantity.toLocaleString()} {ticker} (~{valuePreview})?
      </span>
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setConfirming(false);
          }}
          disabled={pending}
          className="px-2 py-0.5 font-mono text-[10px] uppercase tracking-widest text-text-muted hover:text-text disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-text/40 rounded transition-colors"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={onConfirm}
          disabled={pending}
          className="shrink-0 px-2.5 py-1 font-mono text-[11px] uppercase tracking-widest rounded bg-red/20 border border-red/60 text-red hover:bg-red/30 disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-red/40 transition-colors"
        >
          {pending ? "Selling…" : "Confirm sell"}
        </button>
      </div>
      {error && (
        <span className="text-[10px] font-mono text-red whitespace-nowrap">
          {error}
        </span>
      )}
    </div>
  );
}
