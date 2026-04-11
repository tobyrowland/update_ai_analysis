"use client";

import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
} from "recharts";

interface PsDataPoint {
  date: string;
  ps: number;
}

export default function PsChart({ data }: { data: PsDataPoint[] }) {
  const sorted = [...data].sort(
    (a, b) => new Date(a.date).getTime() - new Date(b.date).getTime()
  );

  return (
    <div className="h-[200px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={sorted}>
          <defs>
            <linearGradient id="psGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#00FF41" stopOpacity={0.3} />
              <stop offset="100%" stopColor="#00FF41" stopOpacity={0} />
            </linearGradient>
          </defs>
          <XAxis
            dataKey="date"
            tick={{ fontSize: 10, fill: "#555555", fontFamily: "monospace" }}
            tickLine={false}
            axisLine={{ stroke: "#222222" }}
            tickFormatter={(d: string) => {
              const date = new Date(d);
              return `${date.getMonth() + 1}/${String(date.getFullYear()).slice(2)}`;
            }}
          />
          <YAxis
            tick={{ fontSize: 10, fill: "#555555", fontFamily: "monospace" }}
            tickLine={false}
            axisLine={{ stroke: "#222222" }}
            domain={["auto", "auto"]}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "#111111",
              border: "1px solid #222222",
              borderRadius: "4px",
              fontFamily: "monospace",
              fontSize: "12px",
            }}
            labelStyle={{ color: "#888888" }}
            itemStyle={{ color: "#00FF41" }}
            formatter={(value) => [Number(value).toFixed(2), "P/S"]}
          />
          <Area
            type="monotone"
            dataKey="ps"
            stroke="#00FF41"
            strokeWidth={1.5}
            fill="url(#psGradient)"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
