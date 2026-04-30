"use client";

import { useId } from "react";

interface SparklineProps {
  data: { x: number; y: number }[];
  color: string;
  // `linear` for the jagged raw-LLM look, `monotone` for smooth equity curves.
  curve?: "linear" | "monotone";
  height?: number;
}

// Hand-rolled SVG sparkline so the homepage bundle doesn't pull in recharts
// (~68 KiB) just to draw a 32-pixel area chart with no axes or tooltips.
export default function Sparkline({
  data,
  color,
  curve = "monotone",
  height = 32,
}: SparklineProps) {
  const gradientId = useId();

  if (data.length < 2) {
    return <div style={{ height }} aria-hidden />;
  }

  // Fixed viewBox stretched to container width via preserveAspectRatio="none".
  // x is evenly spaced on these series, so horizontal distortion is invisible;
  // the stroke stays crisp via vector-effect="non-scaling-stroke".
  const VW = 100;
  const VH = height;
  const padY = 2;
  const innerH = VH - padY * 2;

  let minY = Infinity;
  let maxY = -Infinity;
  for (const p of data) {
    if (p.y < minY) minY = p.y;
    if (p.y > maxY) maxY = p.y;
  }
  const span = maxY - minY || 1;

  const xs = data.map((_, i) => (i / (data.length - 1)) * VW);
  const ys = data.map((p) => padY + (1 - (p.y - minY) / span) * innerH);

  const linePath =
    curve === "monotone" ? buildSmoothPath(xs, ys) : buildLinearPath(xs, ys);
  const areaPath = `${linePath} L ${xs[xs.length - 1]} ${VH} L ${xs[0]} ${VH} Z`;

  return (
    <div style={{ height: VH }} className="w-full">
      <svg
        width="100%"
        height="100%"
        viewBox={`0 0 ${VW} ${VH}`}
        preserveAspectRatio="none"
        aria-hidden
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.25} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        <path d={areaPath} fill={`url(#${gradientId})`} />
        <path
          d={linePath}
          fill="none"
          stroke={color}
          strokeWidth={1.25}
          strokeLinecap="round"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    </div>
  );
}

function buildLinearPath(xs: number[], ys: number[]): string {
  let d = `M ${xs[0]} ${ys[0]}`;
  for (let i = 1; i < xs.length; i++) {
    d += ` L ${xs[i]} ${ys[i]}`;
  }
  return d;
}

// Cubic Bezier with control points at horizontal midpoints — visually
// indistinguishable from recharts' monotone curve at sparkline scale.
function buildSmoothPath(xs: number[], ys: number[]): string {
  let d = `M ${xs[0]} ${ys[0]}`;
  for (let i = 1; i < xs.length; i++) {
    const x0 = xs[i - 1];
    const y0 = ys[i - 1];
    const x1 = xs[i];
    const y1 = ys[i];
    const cx = (x0 + x1) / 2;
    d += ` C ${cx} ${y0}, ${cx} ${y1}, ${x1} ${y1}`;
  }
  return d;
}
