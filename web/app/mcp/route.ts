/**
 * AlphaMolt MCP server (Streamable HTTP transport).
 *
 * Mounted at /mcp. Any MCP-compatible client (Claude Code, Claude Desktop,
 * Cursor, Cline, OpenAI Agents SDK) can connect by adding:
 *
 *   { "mcpServers": { "alphamolt": { "url": "https://alphamolt.ai/mcp" } } }
 *
 * The transport runs in stateless mode (no session IDs) so it scales
 * trivially across Vercel serverless invocations.
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { WebStandardStreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js";
import { z } from "zod";

import { corsHeaders } from "@/lib/api-utils";
import {
  getEquity,
  listEquities,
  searchEquities,
} from "@/lib/equities-query";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 60;

function buildServer(): McpServer {
  const server = new McpServer({
    name: "alphamolt",
    version: "1.0.0",
  });

  server.registerTool(
    "list_equities",
    {
      title: "List equities",
      description:
        "List companies in the AlphaMolt screener, ordered by composite rank (best first). Optional filters narrow by status, sector, or country. Returns lightweight summary rows.",
      inputSchema: {
        status: z
          .string()
          .optional()
          .describe(
            "Filter by screener status (substring, case-insensitive). Examples: 'Eligible', 'Discount', 'New', 'Excluded'.",
          ),
        sector: z.string().optional().describe("Exact sector match."),
        country: z.string().optional().describe("Exact country match."),
        limit: z
          .number()
          .int()
          .min(1)
          .max(1000)
          .optional()
          .describe("Maximum rows to return (default 100, max 1000)."),
        offset: z
          .number()
          .int()
          .min(0)
          .optional()
          .describe("Pagination offset."),
      },
    },
    async ({ status, sector, country, limit, offset }) => {
      const result = await listEquities({
        status,
        sector,
        country,
        limit: limit ?? 100,
        offset,
      });
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    },
  );

  server.registerTool(
    "get_equity",
    {
      title: "Get equity detail",
      description:
        "Get the full AlphaMolt record for a single ticker, including AI narrative, evaluations, fundamentals, flags, and P/S history.",
      inputSchema: {
        ticker: z
          .string()
          .min(1)
          .describe("Ticker symbol (e.g. 'BCRX', 'NVDA')."),
      },
    },
    async ({ ticker }) => {
      const result = await getEquity(ticker);
      if (!result) {
        return {
          isError: true,
          content: [
            {
              type: "text",
              text: `Ticker '${ticker}' not found in AlphaMolt screener.`,
            },
          ],
        };
      }
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    },
  );

  server.registerTool(
    "search_equities",
    {
      title: "Search equities",
      description:
        "Fuzzy search the AlphaMolt screener by ticker or company name. Returns matching summary rows.",
      inputSchema: {
        query: z
          .string()
          .min(1)
          .describe("Search string matched against ticker and company_name."),
        limit: z
          .number()
          .int()
          .min(1)
          .max(100)
          .optional()
          .describe("Maximum results (default 25, max 100)."),
      },
    },
    async ({ query, limit }) => {
      const matches = await searchEquities(query, limit ?? 25);
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(
              { query, count: matches.length, equities: matches },
              null,
              2,
            ),
          },
        ],
      };
    },
  );

  return server;
}

async function handle(request: Request): Promise<Response> {
  // Stateless transport: a fresh server + transport per request keeps Vercel
  // serverless invocations independent. There's no in-memory session state
  // to lose between cold starts.
  const server = buildServer();
  const transport = new WebStandardStreamableHTTPServerTransport({
    sessionIdGenerator: undefined,
    enableJsonResponse: true,
  });

  await server.connect(transport);

  const response = await transport.handleRequest(request);

  // Layer our CORS headers onto whatever the transport returned.
  const headers = new Headers(response.headers);
  for (const [k, v] of Object.entries(corsHeaders)) {
    headers.set(k, v);
  }

  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

export async function GET(request: Request) {
  return handle(request);
}

export async function POST(request: Request) {
  return handle(request);
}

export async function DELETE(request: Request) {
  return handle(request);
}

export async function OPTIONS() {
  return new Response(null, { status: 204, headers: corsHeaders });
}
