import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  async redirects() {
    return [
      { source: "/auth", destination: "/login", permanent: true },
      { source: "/screener", destination: "/discovery", permanent: true },
      { source: "/market-news", destination: "/research", permanent: true },
      { source: "/market-heatmap", destination: "/sectors", permanent: true },
      { source: "/auto-pilot", destination: "/agent", permanent: true },
      { source: "/compare", destination: "/portfolio", permanent: true },
      { source: "/correlation", destination: "/portfolio", permanent: true },
      { source: "/alpha-zoo/:path*", destination: "/agent", permanent: true },
      { source: "/runs/:path*", destination: "/agent", permanent: true },
      { source: "/dashboard", destination: "/agent", permanent: false },
      { source: "/dashboard/:path*", destination: "/agent", permanent: false },
    ];
  },
};

export default nextConfig;
