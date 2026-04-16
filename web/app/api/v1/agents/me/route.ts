/**
 * DELETE /api/v1/agents/me
 *
 * Requires Authorization: Bearer <api_key>
 *
 * Permanently deletes the authenticated agent and all of its dependent rows
 * (agent_accounts, agent_holdings, agent_trades, agent_portfolio_history)
 * via the FK cascades defined in supabase_schema.sql. Idempotent — no-op
 * if the row is already gone.
 *
 * This is irreversible. There's no claim / recovery flow — once the key is
 * gone, the row is gone.
 */

import {
  errorResponse,
  jsonResponse,
  optionsResponse,
} from "@/lib/api-utils";
import { requireAgent } from "@/lib/auth";
import { deleteAgent } from "@/lib/agents-query";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function OPTIONS() {
  return optionsResponse();
}

export async function DELETE(request: Request) {
  const auth = await requireAgent(request);
  if ("error" in auth) return auth.error;

  try {
    await deleteAgent(auth.agent.id);
    return jsonResponse(
      {
        deleted: {
          id: auth.agent.id,
          handle: auth.agent.handle,
        },
        message:
          "Agent and all dependent rows deleted. The API key is now dead.",
      },
      { status: 200 },
    );
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return errorResponse(message, 500);
  }
}
