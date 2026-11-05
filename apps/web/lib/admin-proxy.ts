import "server-only";

import { timingSafeEqual } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { readAdminSession, validSessionMutation } from "@/lib/auth-session";

function equalSecret(provided: string, configured: string) {
  const left = Buffer.from(provided);
  const right = Buffer.from(configured);
  return left.length === right.length && timingSafeEqual(left, right);
}

export async function proxyAdminPost(request: NextRequest, backendPath: string, normalize?: (body: Record<string, unknown>) => unknown) {
  const authMode = (process.env.ADMIN_AUTH_MODE || "local_key").toLowerCase();
  const operatorSecret = process.env.DASHBOARD_ADMIN_TOKEN;
  const backendSecret = process.env.ADMIN_API_KEY;
  let providedCredential = request.headers.get("x-local-admin-token") || "";
  if (authMode === "local_key" && (!operatorSecret || !backendSecret)) {
    return NextResponse.json({ detail: "ยังไม่ได้ตั้งค่า server-side admin credentials" }, { status: 503 });
  }
  // An optimized Next build also runs in local Compose. Only an explicit
  // development deployment may use the local operator credential there.
  const environment = process.env.ENVIRONMENT?.trim().toLowerCase();
  const productionDeployment = environment === "production"
    || (process.env.NODE_ENV === "production" && environment !== "development");
  if (productionDeployment && authMode !== "oidc") {
    return NextResponse.json({ detail: "production ต้องใช้ OIDC สำหรับงานผู้ดูแลระบบ" }, { status: 503 });
  }
  if (authMode === "local_key" && !equalSecret(providedCredential, operatorSecret!)) {
    return NextResponse.json({ detail: "สิทธิ์ผู้ดูแลระบบไม่ถูกต้อง" }, { status: 401 });
  }
  if (authMode === "oidc") {
    try {
      const session = await readAdminSession(request);
      if (!session) return NextResponse.json({ detail: "กรุณาเข้าสู่ระบบผู้ดูแลก่อนทำรายการ" }, { status: 401 });
      if (!validSessionMutation(request, session)) return NextResponse.json({ detail: "คำขอไม่ผ่านการตรวจสอบ CSRF/origin" }, { status: 403 });
      providedCredential = session.accessToken;
    } catch {
      return NextResponse.json({ detail: "ระบบ session ยังไม่พร้อม" }, { status: 503 });
    }
  }
  if (!["local_key", "oidc"].includes(authMode)) {
    return NextResponse.json({ detail: "โหมดการยืนยันตัวตนผู้ดูแลไม่ถูกต้อง" }, { status: 503 });
  }
  const reader = request.body?.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    if (reader) for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 32_768) {
        await reader.cancel();
        return NextResponse.json({ detail: "ข้อมูลคำขอมีขนาดใหญ่เกินกำหนด" }, { status: 413 });
      }
      chunks.push(value);
    }
  } catch {
    return NextResponse.json({ detail: "อ่านข้อมูลคำขอไม่สำเร็จ" }, { status: 400 });
  } finally {
    reader?.releaseLock();
  }
  let body: unknown;
  try { body = JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch { body = null; }
  if (body == null || typeof body !== "object" || Array.isArray(body)) {
    return NextResponse.json({ detail: "ข้อมูลคำขอไม่ถูกต้อง" }, { status: 422 });
  }
  if (normalize) body = normalize(body as Record<string, unknown>);
  if (body == null) return NextResponse.json({ detail: "ข้อมูลคำขอไม่ถูกต้อง" }, { status: 422 });
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
      signal: AbortSignal.timeout(15_000),
    });
    const payload = await response.json().catch(() => ({ detail: `Backend ${response.status}` }));
    return NextResponse.json(payload, { status: response.status });
  } catch {
    return NextResponse.json({ detail: "เชื่อมต่อ backend ไม่สำเร็จ" }, { status: 502 });
  }
}
