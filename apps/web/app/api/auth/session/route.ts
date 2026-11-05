import { NextRequest, NextResponse } from "next/server";
import { readAdminSession } from "@/lib/auth-session";
import { oidcSettings } from "@/lib/oidc";

export async function GET(request: NextRequest) {
  const mode = process.env.ADMIN_AUTH_MODE || "local_key";
  let configured = false;
  try { oidcSettings(); configured = true; } catch { /* Return configuration state without secret values. */ }
  try {
    const session = configured ? await readAdminSession(request) : null;
    return NextResponse.json({ mode, configured, authenticated: Boolean(session), actor: session?.actor, csrf_token: session?.csrfToken, expires_at: session?.expiresAt }, { headers: { "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ mode, configured, authenticated: false, detail: "ระบบ session ยังไม่พร้อม" }, { status: 503, headers: { "Cache-Control": "no-store" } });
  }
}
