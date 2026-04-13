import { corsHeaders } from "@/lib/api-utils";
import { OPENAPI_SPEC } from "@/lib/openapi-spec";

export const runtime = "nodejs";

export async function GET() {
  return new Response(JSON.stringify(OPENAPI_SPEC, null, 2), {
    status: 200,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
      ...corsHeaders,
    },
  });
}

export async function OPTIONS() {
  return new Response(null, { status: 204, headers: corsHeaders });
}
