import { NextRequest, NextResponse } from "next/server";
import { proxyAdminPost } from "@/lib/admin-proxy";

export async function POST(request: NextRequest, context: { params: Promise<{ id: string; action: string }> }) {
  const { id, action } = await context.params;
  if (!/^[a-zA-Z0-9_-]{1,64}$/.test(id) || !["resume", "cancel"].includes(action)) {
    return NextResponse.json({ detail: "รหัสงานหรือคำสั่งไม่ถูกต้อง" }, { status: 422 });
  }
  return proxyAdminPost(request, `/api/ingestion/backfills/${encodeURIComponent(id)}/${action}`);
}
