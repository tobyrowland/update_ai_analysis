import { errorResponse, jsonResponse, optionsResponse } from "@/lib/api-utils";
import { getEquity } from "@/lib/equities-query";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function OPTIONS() {
  return optionsResponse();
}

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ ticker: string }> },
) {
  const { ticker } = await params;
  const decoded = decodeURIComponent(ticker);

  try {
    const result = await getEquity(decoded);
    if (!result) {
      return errorResponse(
        `Ticker '${decoded}' not found in AlphaMolt screener`,
        404,
      );
    }
    return jsonResponse(result);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return errorResponse(message, 500);
  }
}
