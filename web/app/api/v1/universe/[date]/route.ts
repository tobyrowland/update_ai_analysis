/**
 * GET /api/v1/universe/{date}
 *
 * Returns a specific historical universe snapshot. Public, no auth.
 *
 * Path: date as ISO YYYY-MM-DD (the snapshot_date PK column).
 * Query params:
 *   ?detail=compact|extended|full   (default: extended)
 *   ?tickers=NVDA,AAPL,...           (optional in-memory slice)
 *
 * 404 when the (date, detail) pair doesn't exist. No backfill: pre-Phase-1
 * dates return 404 by design.
 */

import {
  errorResponse,
  jsonResponse,
  optionsResponse,
} from "@/lib/api-utils";
import {
  getSnapshotByDate,
  isDetail,
  parseTickerFilter,
  sliceByTickers,
} from "@/lib/universe-query";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export async function OPTIONS() {
  return optionsResponse();
}

export async function GET(
  request: Request,
  { params }: { params: Promise<{ date: string }> },
) {
  const { date } = await params;
  if (!DATE_RE.test(date)) {
    return errorResponse(
      `Invalid date '${date}'. Expected YYYY-MM-DD.`,
      400,
      "invalid_date",
    );
  }

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
    const snap = await getSnapshotByDate(date, detailParam);
    if (!snap) {
      return errorResponse(
        `No snapshot for ${date} at detail='${detailParam}'.`,
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
        // Historical snapshots are doubly immutable — CDN can hold for
        // the full year + serve stale indefinitely.
        headers: {
          "Cache-Control": "public, max-age=31536000, immutable",
        },
      },
    );
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return errorResponse(message, 500);
  }
}
