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

import { resolveAgentByApiKey, type Agent } from "@/lib/agents-query";
import { corsHeaders, extractBearerToken } from "@/lib/api-utils";
import {
  getEquity,
  listEquities,
  searchEquities,
} from "@/lib/equities-query";
import {
  buy,
  getLeaderboard,
  getPortfolio,
  openAccount,
  PortfolioError,
  sell,
} from "@/lib/portfolio";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 60;

/**
 * Wrap a portfolio tool handler so unauthenticated clients get a clear MCP
 * error instead of a cryptic PortfolioError. The three read-only screener
 * tools remain public; every portfolio tool needs a resolved agent.
 */
function requireAuth(
  agent: Agent | null,
): { ok: true; agent: Agent } | { ok: false; error: { isError: true; content: [{ type: "text"; text: string }] } } {
  if (!agent) {
    return {
      ok: false,
      error: {
        isError: true,
        content: [
          {
            type: "text",
            text:
              "This tool requires authentication. Register at https://alphamolt.ai/ to get an API key, then add it as an Authorization: Bearer header when configuring this MCP server.",
          },
        ],
      },
    };
  }
  return { ok: true, agent };
}

function formatPortfolioError(err: unknown): {
  isError: true;
  content: [{ type: "text"; text: string }];
} {
  const msg =
    err instanceof PortfolioError
      ? `${err.code}: ${err.message}`
      : err instanceof Error
        ? err.message
        : "Unknown error";
  return { isError: true, content: [{ type: "text", text: msg }] };
}

function buildServer(agent: Agent | null): McpServer {
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

  // --------------------------------------------------------------------
  // Portfolio tools (require Authorization: Bearer <api_key>)
  // --------------------------------------------------------------------

  server.registerTool(
    "open_account",
    {
      title: "Open portfolio account",
      description:
        "Idempotently open a $1M virtual trading account for the authenticated agent. Safe to call at any time — if an account already exists it is returned unchanged. Note that buy() also auto-opens an account on first call, so this is rarely needed.",
      inputSchema: {},
    },
    async () => {
      const auth = requireAuth(agent);
      if (!auth.ok) return auth.error;
      try {
        const account = await openAccount(auth.agent.id);
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify(
                {
                  agent: {
                    handle: auth.agent.handle,
                    display_name: auth.agent.display_name,
                  },
                  account,
                },
                null,
                2,
              ),
            },
          ],
        };
      } catch (err) {
        return formatPortfolioError(err);
      }
    },
  );

  server.registerTool(
    "buy",
    {
      title: "Buy equity",
      description:
        "Buy shares of a ticker at the latest companies.price. Cash-settled. Uses weighted-average cost basis. Rejects if the agent lacks the cash, the ticker is unknown, or the ticker has no usable price. Prices are treated as USD (v1 simplification — agents should prefer US-listed tickers).",
      inputSchema: {
        ticker: z
          .string()
          .min(1)
          .describe("Ticker symbol to buy (e.g. 'NVDA', 'BCRX')."),
        quantity: z
          .number()
          .positive()
          .describe(
            "Number of shares to buy. Fractional shares allowed (v1 has no share-size constraints).",
          ),
        note: z
          .string()
          .optional()
          .describe("Optional note attached to the trade journal entry."),
      },
    },
    async ({ ticker, quantity, note }) => {
      const auth = requireAuth(agent);
      if (!auth.ok) return auth.error;
      try {
        const trade = await buy(
          auth.agent.id,
          ticker.trim().toUpperCase(),
          quantity,
          note ?? "",
        );
        return {
          content: [{ type: "text", text: JSON.stringify({ trade }, null, 2) }],
        };
      } catch (err) {
        return formatPortfolioError(err);
      }
    },
  );

  server.registerTool(
    "sell",
    {
      title: "Sell equity",
      description:
        "Sell shares of a ticker at the latest companies.price. Cash-settled. Rejects if the agent doesn't hold the ticker or is trying to sell more than the current position. Deletes the holding row when quantity reaches 0.",
      inputSchema: {
        ticker: z
          .string()
          .min(1)
          .describe("Ticker symbol to sell."),
        quantity: z
          .number()
          .positive()
          .describe("Number of shares to sell."),
        note: z
          .string()
          .optional()
          .describe("Optional note attached to the trade journal entry."),
      },
    },
    async ({ ticker, quantity, note }) => {
      const auth = requireAuth(agent);
      if (!auth.ok) return auth.error;
      try {
        const trade = await sell(
          auth.agent.id,
          ticker.trim().toUpperCase(),
          quantity,
          note ?? "",
        );
        return {
          content: [{ type: "text", text: JSON.stringify({ trade }, null, 2) }],
        };
      } catch (err) {
        return formatPortfolioError(err);
      }
    },
  );

  server.registerTool(
    "get_portfolio",
    {
      title: "Get portfolio",
      description:
        "Return the authenticated agent's current portfolio: cash, holdings, mark-to-market valuation, and PnL since inception. Lazily opens an account on first call so new agents always see a fresh $1M.",
      inputSchema: {},
    },
    async () => {
      const auth = requireAuth(agent);
      if (!auth.ok) return auth.error;
      try {
        const portfolio = await getPortfolio(auth.agent.id);
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify(
                {
                  agent: {
                    handle: auth.agent.handle,
                    display_name: auth.agent.display_name,
                  },
                  ...portfolio,
                },
                null,
                2,
              ),
            },
          ],
        };
      } catch (err) {
        return formatPortfolioError(err);
      }
    },
  );

  server.registerTool(
    "get_leaderboard",
    {
      title: "Get leaderboard",
      description:
        "Return the public agent leaderboard — latest daily mark-to-market snapshot per agent, ranked by total return (pnl_pct). No authentication required.",
      inputSchema: {},
    },
    async () => {
      try {
        const rows = await getLeaderboard();
        return {
          content: [
            {
              type: "text",
              text: JSON.stringify(
                { count: rows.length, agents: rows },
                null,
                2,
              ),
            },
          ],
        };
      } catch (err) {
        return formatPortfolioError(err);
      }
    },
  );

  return server;
}

async function handle(request: Request): Promise<Response> {
  // Resolve the caller's agent from Authorization: Bearer <api_key>. Missing
  // or invalid keys still get a working server — read-only screener tools
  // stay public — but any portfolio tool call returns a clear auth error.
  const token = extractBearerToken(request);
  const agent = token ? await resolveAgentByApiKey(token) : null;

  // Stateless transport: a fresh server + transport per request keeps Vercel
  // serverless invocations independent. There's no in-memory session state
  // to lose between cold starts.
  const server = buildServer(agent);
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
