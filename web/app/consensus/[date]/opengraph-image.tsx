import { ImageResponse } from "next/og";
import { getConsensusByDate } from "@/lib/consensus-query";
import { OG_ALT, OG_SIZE, renderConsensusOg } from "@/lib/consensus-og";

// Per-week OG card. Pinned to the requested date so a tweet from
// week N still previews week N's data when clicked in week N+5.
export const runtime = "nodejs";
export const revalidate = 604800;
export const alt = OG_ALT;
export const size = OG_SIZE;
export const contentType = "image/png";

interface ImageProps {
  params: Promise<{ date: string }>;
}

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export default async function Image({ params }: ImageProps) {
  const { date: rawDate } = await params;
  const date = DATE_RE.test(rawDate) ? rawDate : null;
  let snapshot_date: string | null = null;
  let rows: Awaited<ReturnType<typeof getConsensusByDate>>["rows"] = [];

  if (date) {
    try {
      const r = await getConsensusByDate(date);
      snapshot_date = r.snapshot_date;
      rows = r.rows;
    } catch (err) {
      console.error("og /consensus/[date] fetch failed:", err);
    }
  }

  return new ImageResponse(
    renderConsensusOg({ rows, snapshotDate: snapshot_date ?? date }),
    {
      ...size,
      // Explicit CDN cache so X's OG fetcher gets a fast HIT instead of
      // re-rendering on every poll (the route's `revalidate` alone wasn't
      // populating Vercel's edge cache — every request was a MISS at
      // ~1.5s, long enough to tickle X's preview timeout). s-maxage drives
      // Vercel/Cloudflare; stale-while-revalidate keeps it fresh in the
      // background after a snapshot lands.
      headers: {
        "Cache-Control":
          "public, max-age=300, s-maxage=86400, stale-while-revalidate=604800",
      },
    },
  );
}
