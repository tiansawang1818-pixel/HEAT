import { NextRequest, NextResponse } from "next/server";
import * as oidc from "openid-client";
import { openAuthValue } from "@/lib/auth-crypto";
import { authCookieOptions, createAdminSession, deleteAdminSession, loginCookieName, sessionCookieName } from "@/lib/auth-session";
import { oidcConfiguration, oidcSettings, verifyAdminAccessToken } from "@/lib/oidc";

export async function GET(request: NextRequest) {
  let response: NextResponse;
  try {
    const settings = oidcSettings();
    const login = openAuthValue<{ verifier: string; state: string; nonce: string; expiresAt: number }>(request.cookies.get(loginCookieName)?.value || "");
    if (!login || login.expiresAt <= Date.now() || !login.verifier || !login.state || !login.nonce) throw new Error("Login state expired");
    const callback = new URL(settings.redirectUri);
    callback.search = request.nextUrl.search;
    const tokens = await oidc.authorizationCodeGrant(await oidcConfiguration(), callback, {
      pkceCodeVerifier: login.verifier, expectedState: login.state, expectedNonce: login.nonce, idTokenExpected: true,
    });
    const actor = await verifyAdminAccessToken(tokens.access_token);
    const session = await createAdminSession(tokens.access_token, actor, tokens.expires_in ?? 300);
    await deleteAdminSession(request);
    response = NextResponse.redirect(new URL("/observatory", settings.origin));
    response.cookies.set(sessionCookieName, session.id, { ...authCookieOptions, maxAge: session.ttl });
  } catch {
    response = NextResponse.json({ detail: "เข้าสู่ระบบไม่สำเร็จหรือบัญชีนี้ไม่มีสิทธิ์ผู้ดูแล กรุณาเริ่มเข้าสู่ระบบใหม่" }, { status: 401 });
  }
  response.cookies.set(loginCookieName, "", { ...authCookieOptions, maxAge: 0 });
  response.headers.set("Cache-Control", "no-store");
  return response;
}
