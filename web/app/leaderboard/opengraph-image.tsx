import { ImageResponse } from "next/og";
import { getLeaderboard } from "@/lib/leaderboard-query";
import {
  OG_ALT,
  OG_SIZE,
  renderLeaderboardOg,
} from "@/lib/leaderboard-og";

// Dynamic OG card for /leaderboard. Defaults to the 30-day return view —
// same period the page itself opens to.
export const runtime = "nodejs";
export const revalidate = 86400;
export const alt = OG_ALT;
export const size = OG_SIZE;
export const contentType = "image/png";

export default async function Image() {
  let rows: Awaited<ReturnType<typeof getLeaderboard>>["rows"] = [];
  let latestDate: string | null = null;
  try {
    const r = await getLeaderboard();
    rows = r.rows;
    latestDate = r.latestDate;
  } catch (err) {
    // Don't break social previews on a Supabase blip — fall through to
    // the empty-state card.
    console.error("og /leaderboard fetch failed:", err);
  }
  return new ImageResponse(
    renderLeaderboardOg({ rows, period: "30d", snapshotDate: latestDate }),
    {
      ...size,
      headers: {
        "Cache-Control":
          "public, max-age=300, s-maxage=86400, stale-while-revalidate=604800",
      },
    },
  );
}
