/**
 * Shared helpers for public v1 API routes and the MCP endpoint.
 *
 * Goals:
 * - Consistent JSON shape for success and error responses
 * - Permissive CORS so browser-based agents can call us directly
 * - Never leak Supabase internal errors verbatim
 */

export const corsHeaders: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
  "Access-Control-Max-Age": "86400",
};

export function jsonResponse(data: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(data), {
    status: init.status ?? 200,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "public, max-age=60, stale-while-revalidate=300",
      ...corsHeaders,
      ...(init.headers as Record<string, string> | undefined),
    },
  });
}

export function errorResponse(
  message: string,
  status: number,
  code?: string,
): Response {
  return new Response(
    JSON.stringify({
      error: message,
      code: code ?? httpStatusToCode(status),
    }),
    {
      status,
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
        ...corsHeaders,
      },
    },
  );
}

export function optionsResponse(): Response {
  return new Response(null, { status: 204, headers: corsHeaders });
}

function httpStatusToCode(status: number): string {
  if (status === 400) return "bad_request";
  if (status === 401) return "unauthorized";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 429) return "rate_limited";
  if (status >= 500) return "internal_error";
  return "error";
}

/**
 * Extract a Bearer token from a Request's Authorization header.
 *
 * Returns the plaintext token string, or `null` when the header is missing
 * or malformed. Callers should respond with 401 on null.
 */
export function extractBearerToken(request: Request): string | null {
  const header = request.headers.get("authorization");
  if (!header) return null;
  const match = /^bearer\s+(.+)$/i.exec(header.trim());
  if (!match) return null;
  const token = match[1].trim();
  return token.length > 0 ? token : null;
}
