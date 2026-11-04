import "server-only";

import { timingSafeEqual } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";

function equalSecret(provided: string, configured: string) {
  const left = Buffer.from(provided);
  const right = Buffer.from(configured);
  return left.length === right.length && timingSafeEqual(left, right);
}

export async function proxyAdminPost(request: NextRequest, backendPath: string, bodyOverride?: unknown) {
  const operatorSecret = process.env.DASHBOARD_ADMIN_TOKEN;
  const backendSecret = process.env.ADMIN_API_KEY;
  if (!operatorSecret || !backendSecret) {
    return NextResponse.json({ detail: "ยังไม่ได้ตั้งค่า server-side admin credentials" }, { status: 503 });
  }
  if (process.env.NODE_ENV === "production" && (
    operatorSecret === "local-dashboard-admin-change-me"
    || backendSecret === "local-admin-key-change-me"
    || backendSecret === "change-me-in-production"
  )) {
    return NextResponse.json({ detail: "ต้องเปลี่ยน admin credentials ก่อนใช้งาน production" }, { status: 503 });
  }
  if (!equalSecret(request.headers.get("x-local-admin-token") || "", operatorSecret)) {
    return NextResponse.json({ detail: "สิทธิ์ผู้ดูแลระบบไม่ถูกต้อง" }, { status: 401 });
  }
  let body = bodyOverride;
  if (body === undefined) {
    const rawBody = await request.text();
    if (rawBody.length > 32_768) {
      return NextResponse.json({ detail: "ข้อมูลคำขอมีขนาดใหญ่เกินกำหนด" }, { status: 413 });
    }
    try {
      body = JSON.parse(rawBody) as unknown;
    } catch {
      body = null;
    }
  }
  if (body == null || typeof body !== "object") {
    return NextResponse.json({ detail: "ข้อมูลคำขอไม่ถูกต้อง" }, { status: 422 });
  }
  const backend = (process.env.API_INTERNAL_URL || process.env.BACKEND_INTERNAL_URL || "http://localhost:8000").replace(/\/$/, "");
  try {
    const response = await fetch(`${backend}${backendPath}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-Key": backendSecret,
        "X-Admin-Actor": "dashboard-operator",
      },
      body: JSON.stringify(body),
      cache: "no-store",
    });
    const payload = await response.json().catch(() => ({ detail: `Backend ${response.status}` }));
    return NextResponse.json(payload, { status: response.status });
  } catch {
    return NextResponse.json({ detail: "เชื่อมต่อ backend ไม่สำเร็จ" }, { status: 502 });
  }
}
