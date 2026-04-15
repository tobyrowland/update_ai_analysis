/**
 * POST /api/v1/portfolio/buy
 *
 * Body: { ticker: string, quantity: number, note?: string }
 * Requires Authorization: Bearer <api_key>
 *
 * Fills at the latest companies.price, cash-settled, weighted-average cost
 * basis. Rejects on unknown ticker, null price, or insufficient cash.
 */

import {
  errorResponse,
  jsonResponse,
  optionsResponse,
} from "@/lib/api-utils";
import { requireAgent } from "@/lib/auth";
import { buy, PortfolioError } from "@/lib/portfolio";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function OPTIONS() {
  return optionsResponse();
}

export async function POST(request: Request) {
  const auth = await requireAgent(request);
  if ("error" in auth) return auth.error;

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return errorResponse("Request body must be valid JSON", 400, "bad_json");
  }
  if (!body || typeof body !== "object") {
    return errorResponse("Request body must be a JSON object", 400, "bad_body");
  }
  const { ticker, quantity, note } = body as {
    ticker?: unknown;
    quantity?: unknown;
    note?: unknown;
  };
  if (typeof ticker !== "string" || ticker.trim().length === 0) {
    return errorResponse("'ticker' is required", 400, "missing_ticker");
  }
  if (typeof quantity !== "number" || !Number.isFinite(quantity) || quantity <= 0) {
    return errorResponse(
      "'quantity' must be a positive number",
      400,
      "invalid_quantity",
    );
  }
  const noteStr = typeof note === "string" ? note : "";

  try {
    const trade = await buy(auth.agent.id, ticker.trim().toUpperCase(), quantity, noteStr);
    return jsonResponse({ trade }, { status: 201 });
  } catch (err) {
    if (err instanceof PortfolioError) {
      const status = err.code === "insufficient_cash" || err.code === "no_price"
        ? 400
        : err.code === "unknown_ticker"
          ? 404
          : 400;
      return errorResponse(err.message, status, err.code);
    }
    const message = err instanceof Error ? err.message : "Unknown error";
    return errorResponse(message, 500);
  }
}
