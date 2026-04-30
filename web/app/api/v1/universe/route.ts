/**
 * GET /api/v1/universe
 *
 * Returns the latest daily universe snapshot. Public, no auth — same data
 * the LLM agents see at heartbeat time.
 *
 * Query params:
 *   ?detail=compact|extended|full   (default: extended)
 *   ?tickers=NVDA,AAPL,...           (optional in-memory slice)
 *
 * Response: the stored snapshot JSON, plus metadata (sha256, ticker_count,
 * created_at) for cache validation. Cached for 24h on the CDN — snapshots
 * are immutable per (date, detail) so this is safe.
 */

import {
  errorResponse,
  jsonResponse,
  optionsResponse,
} from "@/lib/api-utils";
import {
  getLatestSnapshot,
  isDetail,
  parseTickerFilter,
  sliceByTickers,
} from "@/lib/universe-query";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function OPTIONS() {
  return optionsResponse();
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const detailParam = searchParams.get("detail") ?? "extended";
  if (!isDetail(detailParam)) {
    return errorResponse(
      `Unknown detail tier '${detailParam}'. Expected: compact, extended, full.`,
      400,
      "invalid_detail",
    );
  }

  try {
    const snap = await getLatestSnapshot(detailParam);
    if (!snap) {
      return errorResponse(
        "No universe snapshot available yet. The daily build runs at 06:00 UTC.",
        404,
        "snapshot_not_found",
      );
    }

    const filter = parseTickerFilter(searchParams.get("tickers"));
    const json = filter ? sliceByTickers(snap.json, filter) : snap.json;

    return jsonResponse(
      {
        snapshot_date: snap.snapshot_date,
        detail: snap.detail,
        sha256: snap.sha256,
        ticker_count: json.ticker_count,
        created_at: snap.created_at,
        snapshot: json,
      },
      {
        // Snapshots are immutable per (date, detail). 24h CDN cache is
        // safe — even with the in-memory ticker slice, the response is
        // a deterministic function of (date, detail, tickers) so the
        // CDN keys it correctly via the full URL.
        headers: {
          "Cache-Control": "public, max-age=86400, stale-while-revalidate=86400",
        },
      },
    );
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return errorResponse(message, 500);
  }
}
