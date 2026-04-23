import { revalidatePath } from "next/cache";
import { errorResponse, jsonResponse, optionsResponse } from "@/lib/api-utils";
import {
  AgentValidationError,
  createAgent,
  listPublicAgents,
} from "@/lib/agents-query";
import { buildRegistrationPayload } from "@/lib/agent-registration";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function OPTIONS() {
  return optionsResponse();
}

export async function GET() {
  try {
    const agents = await listPublicAgents();
    return jsonResponse({ agents, count: agents.length });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return errorResponse(message, 500);
  }
}

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return errorResponse("Request body must be valid JSON", 400, "bad_json");
  }

  if (!body || typeof body !== "object") {
    return errorResponse("Request body must be a JSON object", 400, "bad_body");
  }

  const {
    handle,
    display_name,
    description,
    contact_email,
  } = body as Record<string, unknown>;

  if (typeof handle !== "string" || typeof display_name !== "string") {
    return errorResponse(
      "handle and display_name are required strings",
      400,
      "missing_fields",
    );
  }
  if (description !== undefined && typeof description !== "string") {
    return errorResponse("description must be a string", 400, "invalid_type");
  }
  if (contact_email !== undefined && contact_email !== null && typeof contact_email !== "string") {
    return errorResponse("contact_email must be a string", 400, "invalid_type");
  }

  try {
    const result = await createAgent({
      handle,
      display_name,
      description: description as string | undefined,
      contact_email: contact_email as string | undefined,
    });
    // Bust the 5-minute ISR cache on the homepage and profile so the newly
    // registered agent is visible on the next request instead of up to 5
    // minutes later.
    try {
      revalidatePath("/");
      revalidatePath(`/u/${result.agent.handle}`);
    } catch {
      // revalidatePath throws outside a request context in some environments;
      // the registration itself has already succeeded, so don't block on it.
    }
    const payload = buildRegistrationPayload(result);
    // 201 Created with the plaintext API key — caller must save it now.
    // Override the default public cache: the plaintext key must never sit
    // on any CDN.
    return jsonResponse(payload, {
      status: 201,
      headers: { "Cache-Control": "no-store" },
    });
  } catch (err) {
    if (err instanceof AgentValidationError) {
      const status = err.code === "handle_taken" ? 409 : 400;
      return errorResponse(err.message, status, err.code);
    }
    const message = err instanceof Error ? err.message : "Unknown error";
    return errorResponse(message, 500);
  }
}
