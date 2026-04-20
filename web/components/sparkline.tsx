"use client";

import { useId } from "react";
import { ResponsiveContainer, AreaChart, Area } from "recharts";

interface SparklinePoint {
  x: number;
  y: number;
}

interface SparklineProps {
  data: SparklinePoint[];
  color: string;
  // `linear` produces the jagged equity-curve look used for the unhardened
  // control; `monotone` produces the smoothed look used for hardened agents.
  curve?: "linear" | "monotone";
  fillOpacity?: number;
  height?: number;
}

export default function Sparkline({
  data,
  color,
  curve = "monotone",
  fillOpacity = 0.18,
  height = 56,
}: SparklineProps) {
  const gradientId = useId();

  if (data.length === 0) {
    return (
      <div
        style={{ height }}
        className="w-full flex items-center justify-center text-text-muted text-[10px] font-mono"
      >
        no data
      </div>
    );
  }

  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 4, right: 0, bottom: 4, left: 0 }}>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={fillOpacity} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <Area
            type={curve}
            dataKey="y"
            stroke={color}
            strokeWidth={1.5}
            fill={`url(#${gradientId})`}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
