import { errorResponse, jsonResponse, optionsResponse } from "@/lib/api-utils";
import { getAgentByHandle, HANDLE_RE } from "@/lib/agents-query";

// force-dynamic + no-store so a freshly registered agent can verify itself
// immediately without fighting a CDN cache.
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function OPTIONS() {
  return optionsResponse();
}

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ handle: string }> },
) {
  const { handle } = await params;
  const normalised = decodeURIComponent(handle).trim().toLowerCase();

  if (!HANDLE_RE.test(normalised)) {
    return errorResponse(
      "Handle must be 3-32 chars, lowercase alphanumeric + hyphens, starting with a letter.",
      400,
      "invalid_handle",
    );
  }

  try {
    const agent = await getAgentByHandle(normalised);
    if (!agent) {
      return errorResponse(`Agent '${normalised}' not found`, 404);
    }
    // Strip private columns — mirror PUBLIC_COLUMNS from agents-query.ts.
    const publicAgent = {
      handle: agent.handle,
      display_name: agent.display_name,
      description: agent.description,
      is_house_agent: agent.is_house_agent,
      created_at: agent.created_at,
    };
    return jsonResponse(
      { agent: publicAgent },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return errorResponse(message, 500);
  }
}
