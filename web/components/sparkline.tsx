"use client";

import { useId } from "react";
import { ResponsiveContainer, AreaChart, Area } from "recharts";

interface SparklineProps {
  data: { x: number; y: number }[];
  color: string;
  // `linear` for the jagged raw-LLM look, `monotone` for smooth equity curves.
  curve?: "linear" | "monotone";
  height?: number;
}

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

  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart
          data={data}
          margin={{ top: 2, right: 0, bottom: 2, left: 0 }}
        >
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.25} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <Area
            type={curve}
            dataKey="y"
            stroke={color}
            strokeWidth={1.25}
            fill={`url(#${gradientId})`}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
