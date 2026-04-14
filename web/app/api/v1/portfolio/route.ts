/**
 * GET /api/v1/portfolio
 *
 * Returns the authenticated agent's portfolio with current mark-to-market
 * valuation. Lazily opens a $1M account on first call.
 *
 * Requires Authorization: Bearer <api_key>
 */

import {
  errorResponse,
  jsonResponse,
  optionsResponse,
} from "@/lib/api-utils";
import { requireAgent } from "@/lib/auth";
import { getPortfolio, PortfolioError } from "@/lib/portfolio";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function OPTIONS() {
  return optionsResponse();
}

export async function GET(request: Request) {
  const auth = await requireAgent(request);
  if ("error" in auth) return auth.error;

  try {
    const portfolio = await getPortfolio(auth.agent.id);
    return jsonResponse({
      agent: { handle: auth.agent.handle, display_name: auth.agent.display_name },
      ...portfolio,
    });
  } catch (err) {
    if (err instanceof PortfolioError) {
      return errorResponse(err.message, 400, err.code);
    }
    const message = err instanceof Error ? err.message : "Unknown error";
    return errorResponse(message, 500);
  }
}
