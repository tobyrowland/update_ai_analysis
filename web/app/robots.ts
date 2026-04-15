import type { MetadataRoute } from "next";
import { SITE, absoluteUrl } from "@/lib/site";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        // /api is machine-facing. We want agents to hit it via MCP / REST,
        // but there's no reason for search engines to index the JSON bodies.
        // /mcp is the HTTP MCP endpoint — also not useful in search results.
        disallow: ["/api/", "/mcp"],
      },
    ],
    sitemap: absoluteUrl("/sitemap.xml"),
    host: SITE.url,
  };
}
