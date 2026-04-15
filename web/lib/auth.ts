/**
 * Shared auth helper for any route that needs an authenticated agent.
 *
 * Usage:
 *
 *     const auth = await requireAgent(request);
 *     if ("error" in auth) return auth.error;
 *     const agent = auth.agent;
 *
 * Kept deliberately tiny and route-handler-agnostic so both REST routes and
 * the MCP server can share the exact same resolution path.
 */

import type { Agent } from "@/lib/agents-query";
import { resolveAgentByApiKey } from "@/lib/agents-query";
import { errorResponse, extractBearerToken } from "@/lib/api-utils";

export type RequireAgentResult =
  | { agent: Agent }
  | { error: Response };

export async function requireAgent(
  request: Request,
): Promise<RequireAgentResult> {
  const token = extractBearerToken(request);
  if (!token) {
    return {
      error: errorResponse(
        "Missing Authorization header. Send 'Authorization: Bearer ak_live_...'",
        401,
        "missing_auth",
      ),
    };
  }
  const agent = await resolveAgentByApiKey(token);
  if (!agent) {
    return {
      error: errorResponse(
        "API key did not match any registered agent.",
        401,
        "invalid_api_key",
      ),
    };
  }
  return { agent };
}

/**
 * Same as requireAgent but for in-process callers (e.g. the MCP route
 * handler) that already have the plaintext key. Throws on failure with a
 * message suitable for surfacing through the MCP error channel.
 */
export async function requireAgentFromToken(token: string | null): Promise<Agent> {
  if (!token) {
    throw new Error(
      "Missing Authorization header. Send 'Authorization: Bearer ak_live_...'",
    );
  }
  const agent = await resolveAgentByApiKey(token);
  if (!agent) {
    throw new Error("API key did not match any registered agent.");
  }
  return agent;
}
