import { errorResponse, jsonResponse, optionsResponse } from "@/lib/api-utils";
import { listEquities } from "@/lib/equities-query";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function OPTIONS() {
  return optionsResponse();
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);

  try {
    const result = await listEquities({
      status: searchParams.get("status"),
      sector: searchParams.get("sector"),
      country: searchParams.get("country"),
      limit: searchParams.get("limit")
        ? Number(searchParams.get("limit"))
        : undefined,
      offset: searchParams.get("offset")
        ? Number(searchParams.get("offset"))
        : undefined,
    });

    return jsonResponse(result);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return errorResponse(message, 500);
  }
}
