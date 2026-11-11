/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  outputFileTracingRoot: process.cwd(),
  reactStrictMode: true,
  poweredByHeader: false,
  allowedDevOrigins: ["127.0.0.1"],
  async headers() {
    // Framing stays allowed so the site can be embedded in a portfolio page.
    return [{
      source: "/:path*",
      headers: [
        { key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" },
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        { key: "Permissions-Policy", value: "camera=(), microphone=(), payment=()" },
      ],
    }];
  },
  async rewrites() {
    const backend = process.env.API_INTERNAL_URL || process.env.BACKEND_INTERNAL_URL || "http://localhost:8000";
    // App Router's dynamic admin handlers must run before the generic API proxy.
    return { fallback: [{ source: "/api/:path*", destination: `${backend}/api/:path*` }] };
  },
};

export default nextConfig;
