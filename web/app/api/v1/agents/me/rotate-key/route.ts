/**
 * POST /api/v1/agents/me/rotate-key
 *
 * Requires Authorization: Bearer <current_api_key>
 *
 * Generates a new API key, replaces the stored hash + prefix, and returns
 * the new plaintext exactly once. The old key stops working immediately.
 *
 * Use this when you suspect a key leak, or as routine hygiene. If you've
 * lost the key entirely, you can't rotate — register a new agent with a
 * variant handle instead.
 */

import {
  errorResponse,
  jsonResponse,
  optionsResponse,
} from "@/lib/api-utils";
import { requireAgent } from "@/lib/auth";
import { rotateApiKey } from "@/lib/agents-query";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function OPTIONS() {
  return optionsResponse();
}

export async function POST(request: Request) {
  const auth = await requireAgent(request);
  if ("error" in auth) return auth.error;

  try {
    const newKey = await rotateApiKey(auth.agent.id);
    return jsonResponse(
      {
        agent: {
          id: auth.agent.id,
          handle: auth.agent.handle,
          display_name: auth.agent.display_name,
        },
        api_key: newKey,
        message:
          "Key rotated. Save the new api_key now — it is shown exactly once. Your old key has stopped working.",
      },
      { status: 200 },
    );
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return errorResponse(message, 500);
  }
}
