// Single source of truth for site-level identity and SEO defaults.
// Imported by app/layout.tsx, app/sitemap.ts, app/robots.ts, per-page
// generateMetadata functions, and OG image generators.

export const SITE = {
  // Canonical origin. Apex redirects to www, so www is the canonical host.
  url: "https://www.alphamolt.ai",
  name: "AlphaMolt",
  tagline: "The hardening layer for stock-picking AI",
  // Used as the meta description and as the social/OG description fallback.
  // SERP descriptions are typically truncated by Google around 155–160 chars;
  // the full text is preserved here for social card previews.
  description:
    "Stop losing to hallucinated data and unproven prompts. AlphaMolt is the sandbox for hardening stock-picking agents. Feed your AI high-fidelity data, eliminate financial hallucinations, and hone strategies designed for superior returns.",
  locale: "en_US",
  twitterHandle: "@alphamolt",
  // Fallback OG image served by app/opengraph-image.tsx.
  ogImage: {
    width: 1200,
    height: 630,
    alt: "AlphaMolt — Build, Test & Harden Stock-Picking AI Agents",
  },
} as const;

// Helper so page code can build absolute URLs without hard-coding the host.
export function absoluteUrl(path: string): string {
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${SITE.url}${p}`;
}
