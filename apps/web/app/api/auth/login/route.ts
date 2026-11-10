import { NextResponse } from "next/server";
import * as oidc from "openid-client";
import { authCookieOptions, loginCookieName } from "@/lib/auth-session";
import { sealAuthValue } from "@/lib/auth-crypto";
import { oidcConfiguration, oidcSettings } from "@/lib/oidc";

export async function GET() {
  try {
    const settings = oidcSettings();
    const config = await oidcConfiguration();
    const verifier = oidc.randomPKCECodeVerifier();
    const state = oidc.randomState(), nonce = oidc.randomNonce();
    const target = oidc.buildAuthorizationUrl(config, {
      redirect_uri: settings.redirectUri, scope: process.env.OIDC_SCOPES || "openid profile email",
      state, nonce, code_challenge: await oidc.calculatePKCECodeChallenge(verifier), code_challenge_method: "S256",
      ...(process.env.OIDC_RESOURCE ? { resource: process.env.OIDC_RESOURCE } : {}),
      // Auth0 issues a verifiable JWT access token only when the API audience is requested.
      ...(process.env.OIDC_AUDIENCE ? { audience: process.env.OIDC_AUDIENCE } : {}),
    });
    const response = NextResponse.redirect(target);
    response.cookies.set(loginCookieName, sealAuthValue({ verifier, state, nonce, expiresAt: Date.now() + 300_000 }), { ...authCookieOptions, maxAge: 300 });
    response.headers.set("Cache-Control", "no-store");
    return response;
  } catch (error) {
    // Name the failing step without echoing configuration values or secrets.
    const reason = error instanceof Error ? `${error.name}: ${error.message}`.slice(0, 300) : "unknown error";
    console.warn(`SSO login unavailable: ${reason}`);
    return NextResponse.json({ detail: "ยังเชื่อมต่อ SSO ไม่สำเร็จ กรุณาตรวจการตั้งค่ากับผู้ดูแลระบบ" }, { status: 503, headers: { "Cache-Control": "no-store" } });
  }
}
