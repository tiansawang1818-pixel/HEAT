/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  outputFileTracingRoot: process.cwd(),
  reactStrictMode: true,
  poweredByHeader: false,
  allowedDevOrigins: ["127.0.0.1"],
  async rewrites() {
    const backend = process.env.API_INTERNAL_URL || process.env.BACKEND_INTERNAL_URL || "http://localhost:8000";
    // App Router's dynamic admin handlers must run before the generic API proxy.
    return { fallback: [{ source: "/api/:path*", destination: `${backend}/api/:path*` }] };
  },
};

export default nextConfig;
