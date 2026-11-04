import { NextRequest, NextResponse } from "next/server";
import { proxyAdminPost } from "@/lib/admin-proxy";

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ id: string }> },
) {
  const { id } = await context.params;
  if (!/^[a-zA-Z0-9_-]{1,64}$/.test(id)) {
    return NextResponse.json({ detail: "รหัส candidate ไม่ถูกต้อง" }, { status: 422 });
  }
  return proxyAdminPost(request, `/api/candidates/${encodeURIComponent(id)}/approve`);
}
