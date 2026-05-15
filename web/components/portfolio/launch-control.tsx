"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { launchPortfolio } from "@/lib/portfolios-mutations";

export default function LaunchControl({
  launchedAt,
  memberCount,
}: {
  launchedAt: string | null;
  memberCount: number;
}) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  if (launchedAt) {
    const since = new Date(launchedAt).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
    return (
      <div className="rounded-lg border border-green/40 bg-green/5 px-4 py-3">
        <p className="text-[11px] font-mono text-green uppercase tracking-widest mb-1">
          ● Live since {since}
        </p>
        <p className="text-sm text-text-dim leading-relaxed">
          Your portfolio is trading. Its agents rebalance the shared $1M book
          each weekly heartbeat, working to your mandate.
        </p>
      </div>
    );
  }

  function launch() {
    setError(null);
    startTransition(async () => {
      const result = await launchPortfolio();
      if (!result.ok) {
        setError(result.error);
        setConfirming(false);
        return;
      }
      router.refresh();
    });
  }

  return (
    <div className="rounded-lg border border-border bg-bg px-4 py-3">
      <p className="text-[11px] font-mono text-orange uppercase tracking-widest mb-1">
        Draft — not trading yet
      </p>
      <p className="text-sm text-text-dim leading-relaxed mb-3">
        Shape the mandate and assemble your agents below. When you&apos;re
        ready, go live: this grants the portfolio $1M of paper cash and its
        agents start trading at the next heartbeat.
      </p>

      {memberCount === 0 ? (
        <p className="text-xs font-mono text-text-muted">
          Add at least one agent before you can go live.
        </p>
      ) : !confirming ? (
        <button
          type="button"
          onClick={() => setConfirming(true)}
          className="px-4 py-2 bg-green/10 border border-green/40 text-green font-mono text-sm uppercase tracking-widest rounded hover:bg-green/20 hover:border-green transition-colors"
        >
          Go live →
        </button>
      ) : (
        <div className="space-y-2">
          <p className="text-xs font-mono text-orange">
            Going live grants $1M and starts trading — this can&apos;t be undone.
          </p>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={launch}
              disabled={pending}
              className="px-4 py-2 bg-green/10 border border-green/40 text-green font-mono text-sm uppercase tracking-widest rounded hover:bg-green/20 hover:border-green disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {pending ? "Launching…" : "Confirm — go live"}
            </button>
            <button
              type="button"
              onClick={() => setConfirming(false)}
              disabled={pending}
              className="text-xs font-mono text-text-muted hover:text-text"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {error && (
        <div className="mt-3 text-sm text-red font-mono border-l-2 border-red pl-3 py-1">
          {error}
        </div>
      )}
    </div>
  );
}
