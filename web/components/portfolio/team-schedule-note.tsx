"use client";

/**
 * The summary "Team" card's status line: "scheduled · next run Tue 08:00".
 * The next run is the next heartbeat tick for the portfolio's rebalance
 * cadence (migration 051) — daily or weekly — shown in the viewer's local
 * time. Computed after mount (Date.now()) so server and first client render
 * agree — avoiding a hydration mismatch on a time value.
 */

import { useEffect, useState } from "react";
import { nextRunForCadence, shortRunLabel } from "@/lib/agents/schedule";

export default function TeamScheduleNote({
  cadence = "weekly",
}: {
  cadence?: "daily" | "weekly";
}) {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => setNow(Date.now()), []);
  if (now == null) return <>scheduled</>;
  return (
    <>
      {cadence === "daily" ? "daily" : "weekly"} · next run{" "}
      {shortRunLabel(nextRunForCadence(now, cadence))}
    </>
  );
}
