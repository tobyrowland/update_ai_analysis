import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    // Agents often guess the Anthropic-style "well-known" path before landing
    // on the short one — serve the same file under both.
    return [
      { source: "/.well-known/skill.md", destination: "/skill.md" },
    ];
  },
};

export default nextConfig;
