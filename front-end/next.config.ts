import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async redirects() {
    return [
      { source: "/auth", destination: "/login", permanent: true },
      { source: "/screener", destination: "/discovery", permanent: true },
      { source: "/market-news", destination: "/research", permanent: true },
      { source: "/market-heatmap", destination: "/sectors", permanent: true },
      { source: "/auto-pilot", destination: "/ml-fund", permanent: true },
      { source: "/compare", destination: "/portfolio", permanent: true },
      { source: "/correlation", destination: "/portfolio", permanent: true },
      { source: "/alpha-zoo/:path*", destination: "/backtest", permanent: true },
      { source: "/runs/:path*", destination: "/agent", permanent: true },
      { source: "/dashboard/market-focus", destination: "/dashboard", permanent: true },
    ];
  },
};

export default nextConfig;
