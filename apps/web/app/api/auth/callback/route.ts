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
  } catch (error) {
    // Name the failing step without echoing tokens, codes or secrets.
    const reason = error instanceof Error ? `${error.name}: ${error.message}`.slice(0, 300) : "unknown error";
    // Provider error codes and descriptions carry no secrets; they name the misconfiguration.
    const detail = error as { error?: unknown; error_description?: unknown; cause?: { error?: unknown; error_description?: unknown } };
    const code = detail.error ?? detail.cause?.error;
    const description = detail.error_description ?? detail.cause?.error_description
      ?? request.nextUrl.searchParams.get("error_description");
    const cause = code || description
      ? ` (provider: ${String(code ?? request.nextUrl.searchParams.get("error") ?? "").slice(0, 80)} ${String(description ?? "").slice(0, 200)})` : "";
    console.warn(`SSO callback failed: ${reason}${cause}`);
    response = NextResponse.json(
      { detail: "เข้าสู่ระบบไม่สำเร็จหรือบัญชีนี้ไม่มีสิทธิ์ผู้ดูแล กรุณาเริ่มเข้าสู่ระบบใหม่" },
      { status: 401, headers: { "Content-Type": "application/json; charset=utf-8" } },
    );
  }
  response.cookies.set(loginCookieName, "", { ...authCookieOptions, maxAge: 0 });
  response.headers.set("Cache-Control", "no-store");
  return response;
}
