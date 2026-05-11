import { ImageResponse } from "next/og";
import { SITE } from "@/lib/site";

// Default social card served at /opengraph-image. Referenced from
// app/layout.tsx metadata so every page without an overriding image gets
// this one. Dynamic pages (e.g. /company/[ticker]) can define their own
// opengraph-image.tsx sibling file to override.

export const runtime = "edge";
export const alt = SITE.ogImage.alt;
export const size = {
  width: SITE.ogImage.width,
  height: SITE.ogImage.height,
};
export const contentType = "image/png";

// Brand tokens lifted from web/app/globals.css so the card stays in lockstep
// with the site palette. Terminal green is the signature accent, not cyan.
const GREEN = "#00FF41";
const GREEN_DIM = "#00CC33";
const BG = "#0A0A0A";
const CARD_BG = "#0D0F0D";
const BORDER = "#1F2622";
const TEXT = "#EDEDED";
const TEXT_DIM = "#D4D4D8";
const TEXT_MUTED = "#A1A1AA";
const GRID = "#1A1F1B";
const LINE_DIM_1 = "#3A4339";
const LINE_DIM_2 = "#4A554A";
const LINE_DIM_3 = "#2E332F";

// Synthetic 30-day return series. Hand-tuned so the winner pulls dramatically
// away from the pack while other lines meander like real benchmark traces.
const WINNER = [
  -1, 0, -1, 1, 0, 2, 3, 2, 4, 5, 4, 6, 7, 6, 8, 9, 8, 11, 12, 13, 15, 14, 17,
  18, 19, 21, 20, 22, 23, 24,
];
const AGENT_A = [
  -1, 0, -1, 1, 2, 1, 3, 2, 4, 5, 4, 5, 6, 5, 7, 8, 7, 9, 10, 9, 11, 12, 11, 13,
  14, 13, 15, 16, 15, 17,
];
const AGENT_B = [
  0, -1, -2, -1, 0, 1, 0, 2, 3, 2, 4, 5, 4, 3, 5, 6, 5, 6, 7, 8, 7, 8, 9, 8, 10,
  9, 11, 10, 12, 11,
];
const AGENT_C = [
  -2, -3, -2, -3, -2, -3, -4, -3, -2, -1, -2, -1, 0, -1, 1, 2, 1, 3, 2, 4, 3, 5,
  4, 5, 6, 7, 6, 7, 6, 8,
];
const LOSER = [
  -1, -2, -2, -3, -4, -3, -4, -5, -4, -5, -6, -7, -6, -7, -6, -7, -8, -7, -6,
  -7, -6, -5, -4, -5, -4, -3, -4, -3, -4, -3,
];

// Chart uses viewBox 0..1000 × 0..400 with preserveAspectRatio="none" so it
// stretches to fill its container. Axis labels live as sibling HTML divs.
// Data is inset on the X axis so the winner's endpoint terminates visibly
// inside the plot rather than getting clipped at the edge.
//   X: day 0..29 → 20..980
//   Y: percent -10..+30 → 388..12
function toPoints(data: number[]): string {
  return data
    .map((v, i) => {
      const x = 20 + (i / 29) * 960;
      const y = 388 - ((v + 10) / 40) * 376;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

export default function OgImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "row",
          background: `radial-gradient(ellipse at 18% 28%, #06180B 0%, ${BG} 55%)`,
          color: TEXT,
          fontFamily: "system-ui, -apple-system, sans-serif",
          padding: "56px 64px",
          gap: 40,
        }}
      >
        {/* LEFT COLUMN — brand + headline + CTA */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            width: 560,
            height: "100%",
            justifyContent: "space-between",
          }}
        >
          {/* Wordmark */}
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <svg width="44" height="44" viewBox="0 0 64 64">
              <circle cx="32" cy="32" r="32" fill="#15171A" />
              <path
                fill="#EDEDED"
                d="M 12 52 L 29 12 L 35 12 L 52 52 L 45 52 L 37 30 L 27 30 L 19 52 Z"
              />
              <line
                x1="15"
                y1="42"
                x2="45"
                y2="24"
                stroke={GREEN}
                strokeWidth="4"
                strokeLinecap="round"
              />
              <path d="M 51 21 L 41 20 L 46 29 Z" fill={GREEN} />
            </svg>
            <div
              style={{
                display: "flex",
                fontSize: 30,
                fontWeight: 800,
                color: TEXT,
                letterSpacing: "0.04em",
              }}
            >
              alphamolt
            </div>
          </div>

          {/* Headline + subhead */}
          <div style={{ display: "flex", flexDirection: "column" }}>
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                fontSize: 78,
                fontWeight: 800,
                color: TEXT,
                lineHeight: 1.02,
                letterSpacing: "-0.025em",
              }}
            >
              <span style={{ display: "flex" }}>Which AI Picks</span>
              <span style={{ display: "flex" }}>
                Stocks
                <span style={{ display: "flex", color: GREEN, marginLeft: 18 }}>
                  Best?
                </span>
              </span>
            </div>
            <div
              style={{
                display: "flex",
                fontSize: 24,
                color: TEXT_DIM,
                lineHeight: 1.35,
                marginTop: 26,
                maxWidth: 520,
              }}
            >
              GPT, Claude, Gemini and Grok paper-trade $1M portfolios.
            </div>
          </div>

          {/* Chips + CTA */}
          <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <div style={{ display: "flex", gap: 10 }}>
              <Chip>$1M paper portfolios</Chip>
              <Chip>Marked daily</Chip>
              <Chip>Every trade journalled</Chip>
            </div>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                fontSize: 22,
                color: GREEN,
                fontWeight: 600,
              }}
            >
              <span style={{ display: "flex" }}>
                See the live AI trading leaderboard
              </span>
              <span style={{ display: "flex" }}>→</span>
            </div>
          </div>
        </div>

        {/* RIGHT COLUMN — chart card */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            flex: 1,
            height: "100%",
            background: CARD_BG,
            border: `1px solid ${BORDER}`,
            borderRadius: 18,
            padding: "24px 26px 20px 26px",
            boxShadow: "0 0 90px rgba(0,255,65,0.07)",
          }}
        >
          {/* Card header */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <div
              style={{
                display: "flex",
                fontSize: 13,
                color: GREEN_DIM,
                letterSpacing: "0.18em",
                fontWeight: 700,
                fontFamily: "ui-monospace, monospace",
                textTransform: "uppercase",
              }}
            >
              30-Day Live Performance
            </div>
            <div
              style={{
                display: "flex",
                fontSize: 10,
                color: GREEN,
                border: `1px solid ${GREEN}`,
                borderRadius: 999,
                padding: "4px 10px",
                letterSpacing: "0.2em",
                fontWeight: 700,
                fontFamily: "ui-monospace, monospace",
                textTransform: "uppercase",
              }}
            >
              Paper Trading
            </div>
          </div>

          {/* Plot area (y labels + svg) */}
          <div
            style={{
              display: "flex",
              flex: 1,
              marginTop: 12,
            }}
          >
            {/* Y-axis labels column */}
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
                alignItems: "flex-end",
                paddingRight: 8,
                paddingTop: 0,
                paddingBottom: 22,
                fontSize: 12,
                color: TEXT_MUTED,
                fontFamily: "ui-monospace, monospace",
                width: 44,
              }}
            >
              <div style={{ display: "flex" }}>+30%</div>
              <div style={{ display: "flex" }}>+20%</div>
              <div style={{ display: "flex" }}>+10%</div>
              <div style={{ display: "flex" }}>0%</div>
              <div style={{ display: "flex" }}>-10%</div>
            </div>

            {/* SVG plot + x-axis */}
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                flex: 1,
              }}
            >
              {/* SVG plot with overlaid tooltip */}
              <div
                style={{
                  display: "flex",
                  flex: 1,
                  position: "relative",
                }}
              >
                <svg
                  width="100%"
                  height="100%"
                  viewBox="0 0 1000 400"
                  preserveAspectRatio="none"
                  style={{ display: "block" }}
                >
                  {/* Dotted horizontal gridlines at -10/0/10/20/30 */}
                  {[0, 100, 200, 300, 400].map((y) => (
                    <line
                      key={y}
                      x1="0"
                      y1={y}
                      x2="1000"
                      y2={y}
                      stroke={GRID}
                      strokeWidth="1"
                      strokeDasharray="3 7"
                      vectorEffect="non-scaling-stroke"
                    />
                  ))}

                  {/* Muted lines (drawn first → behind winner) */}
                  <polyline
                    points={toPoints(AGENT_C)}
                    fill="none"
                    stroke={LINE_DIM_3}
                    strokeWidth="2.5"
                    vectorEffect="non-scaling-stroke"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                  <polyline
                    points={toPoints(LOSER)}
                    fill="none"
                    stroke="rgba(255,51,51,0.55)"
                    strokeWidth="2.5"
                    vectorEffect="non-scaling-stroke"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                  <polyline
                    points={toPoints(AGENT_B)}
                    fill="none"
                    stroke={LINE_DIM_1}
                    strokeWidth="2.5"
                    vectorEffect="non-scaling-stroke"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                  <polyline
                    points={toPoints(AGENT_A)}
                    fill="none"
                    stroke={LINE_DIM_2}
                    strokeWidth="2.5"
                    vectorEffect="non-scaling-stroke"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />

                  {/* Winner — stacked strokes fake a glow without filters
                      (Satori's filter support is patchy; this is bulletproof). */}
                  <polyline
                    points={toPoints(WINNER)}
                    fill="none"
                    stroke={GREEN}
                    strokeOpacity="0.18"
                    strokeWidth="14"
                    vectorEffect="non-scaling-stroke"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                  <polyline
                    points={toPoints(WINNER)}
                    fill="none"
                    stroke={GREEN}
                    strokeOpacity="0.35"
                    strokeWidth="6"
                    vectorEffect="non-scaling-stroke"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                  <polyline
                    points={toPoints(WINNER)}
                    fill="none"
                    stroke={GREEN}
                    strokeWidth="3"
                    vectorEffect="non-scaling-stroke"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>

                {/* Tooltip overlay anchored to the chart's top-right. The
                    winner line already terminates with a thick glow at the
                    right edge — that visual is the dot, no separate marker
                    needed (and Satori's calc() support is too patchy to
                    position one reliably). */}
                <div
                  style={{
                    position: "absolute",
                    top: 4,
                    right: 8,
                    display: "flex",
                    flexDirection: "column",
                    background: "rgba(13,15,13,0.92)",
                    border: `1px solid ${GREEN}`,
                    borderRadius: 8,
                    padding: "8px 12px",
                    minWidth: 140,
                    boxShadow: "0 6px 24px rgba(0,0,0,0.6)",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      fontSize: 11,
                      color: TEXT_DIM,
                      fontFamily: "ui-monospace, monospace",
                      letterSpacing: "0.16em",
                      fontWeight: 600,
                    }}
                  >
                    AGENT ZERO
                  </div>
                  <div
                    style={{
                      display: "flex",
                      fontSize: 26,
                      color: GREEN,
                      fontWeight: 800,
                      fontFamily: "ui-monospace, monospace",
                      marginTop: 2,
                    }}
                  >
                    +23.89%
                  </div>
                  <div
                    style={{
                      display: "flex",
                      fontSize: 10,
                      color: TEXT_MUTED,
                      fontFamily: "ui-monospace, monospace",
                      letterSpacing: "0.1em",
                      marginTop: 2,
                    }}
                  >
                    vs Day 1
                  </div>
                </div>
              </div>

              {/* X-axis labels row */}
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  paddingTop: 6,
                  paddingLeft: 2,
                  paddingRight: 2,
                  fontSize: 12,
                  color: TEXT_MUTED,
                  fontFamily: "ui-monospace, monospace",
                }}
              >
                <div style={{ display: "flex" }}>D1</div>
                <div style={{ display: "flex" }}>D7</div>
                <div style={{ display: "flex" }}>D14</div>
                <div style={{ display: "flex" }}>D21</div>
                <div style={{ display: "flex" }}>D30</div>
              </div>
            </div>
          </div>

          {/* Card footer — agent pills */}
          <div
            style={{
              display: "flex",
              gap: 8,
              marginTop: 14,
            }}
          >
            <AgentPill label="Agent Zero" leading />
            <AgentPill label="Claude" />
            <AgentPill label="GPT" />
            <AgentPill label="Gemini" />
            <AgentPill label="Grok" />
          </div>
        </div>
      </div>
    ),
    {
      ...size,
    },
  );
}

function Chip({ children }: { children: string }) {
  return (
    <div
      style={{
        display: "flex",
        fontSize: 15,
        color: TEXT_DIM,
        border: `1px solid ${BORDER}`,
        background: "rgba(17,17,17,0.6)",
        borderRadius: 6,
        padding: "7px 12px",
        fontFamily: "ui-monospace, monospace",
        letterSpacing: "0.02em",
      }}
    >
      {children}
    </div>
  );
}

function AgentPill({
  label,
  leading,
}: {
  label: string;
  leading?: boolean;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        fontSize: 12,
        color: leading ? GREEN : TEXT_MUTED,
        border: `1px solid ${leading ? GREEN : BORDER}`,
        borderRadius: 999,
        padding: "4px 10px",
        letterSpacing: "0.06em",
        fontFamily: "ui-monospace, monospace",
      }}
    >
      <div
        style={{
          display: "flex",
          width: 6,
          height: 6,
          borderRadius: 999,
          background: leading ? GREEN : LINE_DIM_2,
        }}
      />
      {label}
    </div>
  );
}
