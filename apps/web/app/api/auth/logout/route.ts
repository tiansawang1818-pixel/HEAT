import { NextRequest, NextResponse } from "next/server";
import { authCookieOptions, deleteAdminSession, readAdminSession, sessionCookieName, validSessionMutation } from "@/lib/auth-session";

export async function POST(request: NextRequest) {
  try {
    const session = await readAdminSession(request);
    if (session && !validSessionMutation(request, session)) return NextResponse.json({ detail: "คำขอออกจากระบบไม่ผ่านการตรวจสอบ" }, { status: 403 });
    await deleteAdminSession(request);
    const response = NextResponse.json({ authenticated: false });
    response.cookies.set(sessionCookieName, "", { ...authCookieOptions, maxAge: 0 });
    response.headers.set("Cache-Control", "no-store");
    return response;
  } catch {
    return NextResponse.json({ detail: "ออกจากระบบไม่สำเร็จ กรุณาลองอีกครั้ง" }, { status: 503 });
  }
}
