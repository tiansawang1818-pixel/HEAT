import "server-only";

import { timingSafeEqual } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";

function equalSecret(provided: string, configured: string) {
  const left = Buffer.from(provided);
  const right = Buffer.from(configured);
  return left.length === right.length && timingSafeEqual(left, right);
}

export async function proxyAdminPost(request: NextRequest, backendPath: string, bodyOverride?: unknown) {
  const authMode = (process.env.ADMIN_AUTH_MODE || "local_key").toLowerCase();
  const operatorSecret = process.env.DASHBOARD_ADMIN_TOKEN;
  const backendSecret = process.env.ADMIN_API_KEY;
  const providedCredential = request.headers.get("x-local-admin-token") || "";
  if (authMode === "local_key" && (!operatorSecret || !backendSecret)) {
    return NextResponse.json({ detail: "ยังไม่ได้ตั้งค่า server-side admin credentials" }, { status: 503 });
  }
  if (process.env.NODE_ENV === "production" && authMode !== "oidc") {
    return NextResponse.json({ detail: "production ต้องใช้ OIDC สำหรับงานผู้ดูแลระบบ" }, { status: 503 });
  }
  if (authMode === "local_key" && !equalSecret(providedCredential, operatorSecret!)) {
    return NextResponse.json({ detail: "สิทธิ์ผู้ดูแลระบบไม่ถูกต้อง" }, { status: 401 });
  }
  if (authMode === "oidc" && (!providedCredential || providedCredential.length > 16_384)) {
    return NextResponse.json({ detail: "ต้องใช้ OIDC access token ที่ถูกต้อง" }, { status: 401 });
  }
  if (!["local_key", "oidc"].includes(authMode)) {
    return NextResponse.json({ detail: "โหมดการยืนยันตัวตนผู้ดูแลไม่ถูกต้อง" }, { status: 503 });
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
    const backendHeaders: Record<string, string> = { "Content-Type": "application/json" };
    if (authMode === "oidc") {
      backendHeaders.Authorization = `Bearer ${providedCredential}`;
    } else {
      backendHeaders["X-Admin-Key"] = backendSecret!;
      backendHeaders["X-Admin-Actor"] = "dashboard-operator";
    }
    const response = await fetch(`${backend}${backendPath}`, {
      method: "POST",
      headers: backendHeaders,
      body: JSON.stringify(body),
      cache: "no-store",
    });
    const payload = await response.json().catch(() => ({ detail: `Backend ${response.status}` }));
    return NextResponse.json(payload, { status: response.status });
  } catch {
    return NextResponse.json({ detail: "เชื่อมต่อ backend ไม่สำเร็จ" }, { status: 502 });
  }
}
