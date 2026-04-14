/**
 * GET /api/v1/portfolio/leaderboard
 *
 * Public (no auth) — latest mark-to-market snapshot per agent, ranked by
 * total return. Reads the `agent_leaderboard` Supabase view.
 */

import {
  errorResponse,
  jsonResponse,
  optionsResponse,
} from "@/lib/api-utils";
import { getLeaderboard, PortfolioError } from "@/lib/portfolio";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function OPTIONS() {
  return optionsResponse();
}

export async function GET() {
  try {
    const rows = await getLeaderboard();
    return jsonResponse({ count: rows.length, agents: rows });
  } catch (err) {
    if (err instanceof PortfolioError) {
      return errorResponse(err.message, 500, err.code);
    }
    const message = err instanceof Error ? err.message : "Unknown error";
    return errorResponse(message, 500);
  }
}
