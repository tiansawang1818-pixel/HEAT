import { NextRequest, NextResponse } from "next/server";

const allowedSources = new Set(["landsat", "sentinel", "dynamic-world", "open-meteo", "worldpop", "osm"]);

export async function POST(request: NextRequest) {
  const operatorSecret = process.env.DASHBOARD_ADMIN_TOKEN;
  const backendSecret = process.env.ADMIN_API_KEY;
  if (!operatorSecret || !backendSecret) {
    return NextResponse.json({ detail: "ยังไม่ได้ตั้งค่า server-side admin credentials" }, { status: 503 });
  }
  if (request.headers.get("x-local-admin-token") !== operatorSecret) {
    return NextResponse.json({ detail: "สิทธิ์ผู้ดูแลระบบไม่ถูกต้อง" }, { status: 401 });
  }
  const body = await request.json().catch(() => ({})) as { source?: string };
  if (!body.source || !allowedSources.has(body.source)) {
    return NextResponse.json({ detail: "แหล่งข้อมูลไม่ถูกต้อง" }, { status: 422 });
  }
  const backend = (process.env.API_INTERNAL_URL || process.env.BACKEND_INTERNAL_URL || "http://localhost:8000").replace(/\/$/, "");
  try {
    const response = await fetch(`${backend}/api/ingestion/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Admin-Key": backendSecret },
      body: JSON.stringify({ source: body.source }),
      cache: "no-store",
    });
    const payload = await response.json().catch(() => ({ detail: `Backend ${response.status}` }));
    return NextResponse.json(payload, { status: response.status });
  } catch {
    return NextResponse.json({ detail: "เชื่อมต่อ backend ไม่สำเร็จ" }, { status: 502 });
  }
}
