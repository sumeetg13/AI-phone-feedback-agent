import type { NextConfig } from "next";

// The browser only ever talks to this Next.js app; /api/* is proxied to the
// FastAPI feedback-service, so no CORS setup is needed on the backend.
const FEEDBACK_API_URL = process.env.FEEDBACK_API_URL ?? "http://localhost:8010";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${FEEDBACK_API_URL}/:path*` }];
  },
};

export default nextConfig;
