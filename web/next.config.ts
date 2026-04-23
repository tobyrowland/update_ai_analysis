import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async redirects() {
    return [
      {
        source: "/skill.md",
        destination: "/api-reference.md",
        permanent: true,
      },
    ];
  },
};

export default nextConfig;
