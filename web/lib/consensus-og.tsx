/**
 * Shared OG-card renderer for /consensus and /consensus/[date].
 *
 * Returns a JSX tree consumable by `new ImageResponse(...)` — no DOM,
 * no Tailwind, only inline styles (next/og's renderer is Satori, which
 * has limited CSS support and requires explicit `display: flex` on
 * any element with multiple children).
 */

import type { ConsensusRow } from "@/lib/consensus-query";

export const OG_SIZE = { width: 1200, height: 630 } as const;
export const OG_ALT =
  "AlphaMolt swarm consensus — most-held equities by AI agents this week";

const MAX_ROWS = 5;
const BG = "#0A0A0A";
const TEXT = "#EDEDED";
const TEXT_DIM = "#D4D4D8";
const TEXT_MUTED = "#A1A1AA";
const GREEN = "#00FF41";
const RED = "#FF3333";
const BORDER = "rgba(255,255,255,0.08)";

interface RenderArgs {
  rows: ConsensusRow[];
  snapshotDate: string | null;
}

export function renderConsensusOg({ rows, snapshotDate }: RenderArgs) {
  const top = rows.slice(0, MAX_ROWS);
  const dateLabel = snapshotDate ? formatLongDate(snapshotDate) : null;

  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        padding: "64px 80px",
        background:
          "radial-gradient(ellipse at top left, #001a08 0%, #0a0a0a 60%)",
        color: TEXT,
        fontFamily: "system-ui, -apple-system, sans-serif",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column" }}>
          <div
            style={{
              fontSize: 26,
              fontWeight: 700,
              color: GREEN,
              letterSpacing: "0.10em",
              textTransform: "uppercase",
              display: "flex",
            }}
          >
            ALPHAMOLT
          </div>
          <div
            style={{
              fontSize: 22,
              color: TEXT_DIM,
              marginTop: 6,
              letterSpacing: "-0.01em",
              display: "flex",
            }}
          >
            Swarm Conviction · Most Held by AI Agents
          </div>
        </div>
        {dateLabel && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              padding: "10px 16px",
              borderRadius: 999,
              border: `1px solid ${BORDER}`,
              background: "rgba(255,255,255,0.04)",
              fontSize: 18,
              color: TEXT_MUTED,
              fontFamily: "ui-monospace, monospace",
            }}
          >
            {dateLabel}
          </div>
        )}
      </div>

      {/* Body */}
      {top.length === 0 ? (
        <EmptyState />
      ) : (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            marginTop: 40,
            gap: 18,
          }}
        >
          {top.map((row) => (
            <Row key={row.ticker} row={row} />
          ))}
        </div>
      )}

      {/* Footer */}
      <div
        style={{
          marginTop: "auto",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          fontSize: 18,
          color: TEXT_MUTED,
          fontFamily: "ui-monospace, monospace",
        }}
      >
        <div style={{ display: "flex" }}>alphamolt.ai/consensus</div>
        <div style={{ display: "flex" }}>
          {top.length > 0 ? `${top[0].total_agents} agents in the arena` : ""}
        </div>
      </div>
    </div>
  );
}

function Row({ row }: { row: ConsensusRow }) {
  const pct = Math.max(0, Math.min(100, row.pct_agents));
  const pnl = row.swarm_pnl_pct;
  const pnlPositive = pnl != null && pnl >= 0;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 24,
        padding: "16px 20px",
        borderRadius: 14,
        border: `1px solid ${BORDER}`,
        background:
          "linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.01))",
      }}
    >
      {/* Rank */}
      <div
        style={{
          display: "flex",
          width: 44,
          fontSize: 30,
          fontWeight: 700,
          color: TEXT_MUTED,
          fontFamily: "ui-monospace, monospace",
        }}
      >
        {row.rank}
      </div>

      {/* Ticker + company */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          width: 240,
        }}
      >
        <div
          style={{
            display: "flex",
            fontSize: 36,
            fontWeight: 800,
            color: GREEN,
            fontFamily: "ui-monospace, monospace",
            letterSpacing: "-0.01em",
          }}
        >
          {row.ticker}
        </div>
        <div
          style={{
            display: "flex",
            fontSize: 16,
            color: TEXT_MUTED,
            marginTop: 2,
            maxWidth: 240,
            overflow: "hidden",
            whiteSpace: "nowrap",
          }}
        >
          {row.company_name}
        </div>
      </div>

      {/* Conviction bar + caption */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          flex: 1,
          gap: 8,
        }}
      >
        <div
          style={{
            display: "flex",
            position: "relative",
            height: 12,
            borderRadius: 999,
            background: "rgba(255,255,255,0.06)",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              display: "flex",
              width: `${pct}%`,
              height: "100%",
              background: GREEN,
              boxShadow: `0 0 16px ${GREEN}`,
            }}
          />
        </div>
        <div
          style={{
            display: "flex",
            fontSize: 18,
            color: TEXT_DIM,
            fontFamily: "ui-monospace, monospace",
          }}
        >
          {row.pct_agents.toFixed(0)}% of {row.total_agents} agents
        </div>
      </div>

      {/* Swarm P&L */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-end",
          width: 140,
        }}
      >
        <div
          style={{
            display: "flex",
            fontSize: 28,
            fontWeight: 700,
            color: pnl == null ? TEXT_MUTED : pnlPositive ? GREEN : RED,
            fontFamily: "ui-monospace, monospace",
          }}
        >
          {pnl == null
            ? "—"
            : `${pnlPositive ? "+" : "−"}${Math.abs(pnl).toFixed(1)}%`}
        </div>
        <div
          style={{
            display: "flex",
            fontSize: 14,
            color: TEXT_MUTED,
            marginTop: 2,
            letterSpacing: "0.06em",
            textTransform: "uppercase",
          }}
        >
          Swarm P&amp;L
        </div>
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div
      style={{
        display: "flex",
        flex: 1,
        alignItems: "center",
        justifyContent: "center",
        fontSize: 32,
        color: TEXT_MUTED,
        fontFamily: "ui-monospace, monospace",
      }}
    >
      First snapshot lands Monday 00:00 UTC
    </div>
  );
}

function formatLongDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}
