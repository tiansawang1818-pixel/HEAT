import { NextRequest, NextResponse } from "next/server";
import { proxyAdminPost } from "@/lib/admin-proxy";

const allowedSources = new Set(["bma-boundaries", "landsat", "sentinel", "dynamic-world", "open-meteo", "worldpop", "osm"]);

export async function POST(request: NextRequest) {
  const body = await request.json().catch(() => ({})) as { source?: string };
  if (!body.source || !allowedSources.has(body.source)) {
    return NextResponse.json({ detail: "แหล่งข้อมูลไม่ถูกต้อง" }, { status: 422 });
  }
  return proxyAdminPost(request, "/api/ingestion/run", { source: body.source });
}
