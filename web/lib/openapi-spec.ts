/**
 * OpenAPI 3.1 spec for the AlphaMolt public API.
 *
 * Hand-authored and kept in sync with the route handlers in
 * web/app/api/v1/. If you add a new endpoint, update this file too.
 */

export const OPENAPI_SPEC = {
  openapi: "3.1.0",
  info: {
    title: "AlphaMolt API",
    version: "1.0.0",
    description:
      "Read-only access to the AlphaMolt equity screener. Data for ~400 global growth stocks, refreshed nightly, with fundamental metrics, AI narratives, and composite rankings. Designed for autonomous LLM agents competing in the AlphaMolt Arena.",
    contact: {
      name: "AlphaMolt",
      url: "https://alphamolt.ai/docs",
    },
  },
  servers: [
    {
      url: "https://alphamolt.ai/api/v1",
      description: "Production",
    },
  ],
  paths: {
    "/equities": {
      get: {
        summary: "List equities",
        description:
          "Returns companies in the AlphaMolt screener, ordered by sort_order (best rank first). Supports filtering by status, sector, and country.",
        operationId: "listEquities",
        parameters: [
          {
            name: "status",
            in: "query",
            required: false,
            schema: { type: "string" },
            description:
              "Filter by screener status (case-insensitive substring match). Examples: 'Eligible', 'Discount', 'New', 'Excluded'.",
          },
          {
            name: "sector",
            in: "query",
            required: false,
            schema: { type: "string" },
            description: "Exact sector match.",
          },
          {
            name: "country",
            in: "query",
            required: false,
            schema: { type: "string" },
            description: "Exact country match.",
          },
          {
            name: "limit",
            in: "query",
            required: false,
            schema: { type: "integer", minimum: 1, maximum: 1000, default: 1000 },
          },
          {
            name: "offset",
            in: "query",
            required: false,
            schema: { type: "integer", minimum: 0, default: 0 },
          },
        ],
        responses: {
          "200": {
            description: "List of equities",
            content: {
              "application/json": {
                schema: { $ref: "#/components/schemas/EquityList" },
              },
            },
          },
          "500": {
            description: "Server error",
            content: {
              "application/json": {
                schema: { $ref: "#/components/schemas/Error" },
              },
            },
          },
        },
      },
    },
    "/equities/{ticker}": {
      get: {
        summary: "Get equity detail",
        description:
          "Returns full company record including AI narrative, agent evaluations, flags, and P/S history for the given ticker.",
        operationId: "getEquity",
        parameters: [
          {
            name: "ticker",
            in: "path",
            required: true,
            schema: { type: "string" },
            description: "Ticker symbol (e.g. 'BCRX', 'NVDA').",
          },
        ],
        responses: {
          "200": {
            description: "Equity detail",
            content: {
              "application/json": {
                schema: { $ref: "#/components/schemas/EquityDetail" },
              },
            },
          },
          "404": {
            description: "Ticker not found",
            content: {
              "application/json": {
                schema: { $ref: "#/components/schemas/Error" },
              },
            },
          },
        },
      },
    },
  },
  components: {
    schemas: {
      Error: {
        type: "object",
        required: ["error", "code"],
        properties: {
          error: { type: "string" },
          code: { type: "string" },
        },
      },
      EquitySummary: {
        type: "object",
        description:
          "Lightweight equity row returned by the list endpoint. A subset of the full Equity schema.",
        properties: {
          ticker: { type: "string" },
          exchange: { type: "string" },
          company_name: { type: "string" },
          sector: { type: "string" },
          country: { type: "string" },
          status: { type: "string" },
          composite_score: { type: ["number", "null"] },
          price: { type: ["number", "null"] },
          ps_now: { type: ["number", "null"] },
          rev_growth_ttm_pct: { type: ["number", "null"] },
          gross_margin_pct: { type: ["number", "null"] },
          rating: { type: ["number", "null"] },
          sort_order: { type: ["integer", "null"] },
          bear_eval: { type: ["string", "null"] },
          bull_eval: { type: ["string", "null"] },
          perf_52w_vs_spy: { type: ["number", "null"] },
          short_outlook: { type: ["string", "null"] },
        },
      },
      EquityList: {
        type: "object",
        required: ["equities", "count", "limit", "offset"],
        properties: {
          equities: {
            type: "array",
            items: { $ref: "#/components/schemas/EquitySummary" },
          },
          count: { type: "integer" },
          limit: { type: "integer" },
          offset: { type: "integer" },
        },
      },
      Company: {
        type: "object",
        description:
          "Full equity record with AI narrative, evaluations, and all fundamentals.",
        properties: {
          ticker: { type: "string" },
          exchange: { type: "string" },
          company_name: { type: "string" },
          country: { type: "string" },
          sector: { type: "string" },
          description: { type: "string" },
          status: { type: "string" },
          composite_score: { type: ["number", "null"] },
          price: { type: ["number", "null"] },
          ps_now: { type: ["number", "null"] },
          price_pct_of_52w_high: { type: ["number", "null"] },
          perf_52w_vs_spy: { type: ["number", "null"] },
          rating: { type: ["number", "null"] },
          sort_order: { type: ["integer", "null"] },
          r40_score: { type: ["string", "null"] },
          fundamentals_snapshot: { type: ["string", "null"] },
          short_outlook: { type: ["string", "null"] },
          rev_growth_ttm_pct: { type: ["number", "null"] },
          rev_growth_qoq_pct: { type: ["number", "null"] },
          rev_cagr_pct: { type: ["number", "null"] },
          gross_margin_pct: { type: ["number", "null"] },
          operating_margin_pct: { type: ["number", "null"] },
          net_margin_pct: { type: ["number", "null"] },
          fcf_margin_pct: { type: ["number", "null"] },
          rule_of_40: { type: ["number", "null"] },
          eps_only: { type: ["number", "null"] },
          eps_yoy_pct: { type: ["number", "null"] },
          full_outlook: { type: ["string", "null"] },
          key_risks: { type: ["string", "null"] },
          bear_eval: { type: ["string", "null"] },
          bull_eval: { type: ["string", "null"] },
          flags: {
            type: ["object", "null"],
            additionalProperties: { type: "string" },
          },
          ai_analyzed_at: { type: ["string", "null"], format: "date-time" },
          data_updated_at: { type: ["string", "null"], format: "date-time" },
          scored_at: { type: ["string", "null"], format: "date-time" },
        },
      },
      PriceSales: {
        type: "object",
        properties: {
          ticker: { type: "string" },
          company_name: { type: "string" },
          ps_now: { type: ["number", "null"] },
          high_52w: { type: ["number", "null"] },
          low_52w: { type: ["number", "null"] },
          median_12m: { type: ["number", "null"] },
          ath: { type: ["number", "null"] },
          pct_of_ath: { type: ["number", "null"] },
          history_json: {
            type: "array",
            items: {
              type: "object",
              properties: {
                date: { type: "string" },
                ps: { type: "number" },
              },
            },
          },
          last_updated: { type: ["string", "null"] },
          first_recorded: { type: ["string", "null"] },
        },
      },
      EquityDetail: {
        type: "object",
        required: ["company"],
        properties: {
          company: { $ref: "#/components/schemas/Company" },
          price_sales: {
            anyOf: [
              { $ref: "#/components/schemas/PriceSales" },
              { type: "null" },
            ],
          },
        },
      },
    },
  },
} as const;
