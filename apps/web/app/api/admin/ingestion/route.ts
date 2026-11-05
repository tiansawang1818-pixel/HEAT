import { NextRequest } from "next/server";
import { proxyAdminPost } from "@/lib/admin-proxy";

const allowedSources = new Set(["bma-boundaries", "landsat", "sentinel", "dynamic-world", "open-meteo", "worldpop", "osm"]);

export async function POST(request: NextRequest) {
  return proxyAdminPost(request, "/api/ingestion/run", (body) =>
    typeof body.source === "string" && allowedSources.has(body.source) ? { source: body.source } : null);
}
