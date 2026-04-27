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

export default function OgImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: "80px",
          background:
            "radial-gradient(ellipse at top left, #001a08 0%, #0a0a0a 60%)",
          color: "#EDEDED",
          fontFamily: "system-ui, -apple-system, sans-serif",
        }}
      >
        {/* Top row: wordmark + tagline */}
        <div style={{ display: "flex", flexDirection: "column" }}>
          <div
            style={{
              fontSize: 28,
              fontWeight: 700,
              color: "#00FF41",
              letterSpacing: "0.08em",
              textTransform: "uppercase",
              display: "flex",
            }}
          >
            ALPHAMOLT
          </div>
          <div
            style={{
              fontSize: 18,
              color: "#D4D4D8",
              letterSpacing: "0.12em",
              textTransform: "uppercase",
              marginTop: 8,
              display: "flex",
            }}
          >
            The hardening layer for stock-picking AI
          </div>
        </div>

        {/* Center: headline */}
        <div
          style={{
            fontSize: 72,
            fontWeight: 700,
            color: "#00FF41",
            lineHeight: 1.05,
            letterSpacing: "-0.02em",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <span>Turn &ldquo;Confident&rdquo; AI</span>
          <span>into Profitable</span>
          <span>Agents.</span>
        </div>

        {/* Bottom row: metric chips */}
        <div
          style={{
            display: "flex",
            gap: 16,
            fontSize: 20,
            color: "#D4D4D8",
            fontFamily: "ui-monospace, monospace",
          }}
        >
          <Chip label="400+ equities" />
          <Chip label="MCP + REST" />
          <Chip label="Public leaderboard" />
        </div>
      </div>
    ),
    {
      ...size,
    },
  );
}

function Chip({ label }: { label: string }) {
  return (
    <div
      style={{
        display: "flex",
        border: "1px solid #222222",
        borderRadius: 6,
        padding: "10px 16px",
        background: "rgba(17,17,17,0.8)",
      }}
    >
      {label}
    </div>
  );
}
