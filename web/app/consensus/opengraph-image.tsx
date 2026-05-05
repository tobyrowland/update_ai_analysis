import { ImageResponse } from "next/og";
import { getLatestConsensus } from "@/lib/consensus-query";
import { OG_ALT, OG_SIZE, renderConsensusOg } from "@/lib/consensus-og";

// Dynamic OG card for /consensus — renders the latest snapshot's top
// picks. Regenerated daily; comfortably within the weekly snapshot
// cadence so a fresh image lands within a day of consensus_snapshot.py.
export const runtime = "nodejs";
export const revalidate = 86400;
export const alt = OG_ALT;
export const size = OG_SIZE;
export const contentType = "image/png";

export default async function Image() {
  let snapshot_date: string | null = null;
  let rows: Awaited<ReturnType<typeof getLatestConsensus>>["rows"] = [];
  try {
    const r = await getLatestConsensus();
    snapshot_date = r.snapshot_date;
    rows = r.rows;
  } catch (err) {
    // Don't break social previews on a Supabase blip — fall through to
    // the empty-state card.
    console.error("og /consensus fetch failed:", err);
  }
  return new ImageResponse(renderConsensusOg({ rows, snapshotDate: snapshot_date }), {
    ...size,
  });
}
