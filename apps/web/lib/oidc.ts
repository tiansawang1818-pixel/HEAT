import "server-only";
import * as oidc from "openid-client";

export function oidcSettings() {
  const origin = new URL(process.env.APP_ORIGIN || "");
  const issuer = new URL(process.env.OIDC_ISSUER || "");
  const clientId = process.env.OIDC_CLIENT_ID || "";
  if ((process.env.ADMIN_AUTH_MODE || "local_key") !== "oidc" || !clientId) throw new Error("SSO is not configured");
  for (const url of [origin, issuer]) {
    const localDevelopment = process.env.NODE_ENV !== "production" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
    if ((url.protocol !== "https:" && !localDevelopment) || url.username || url.password) throw new Error("SSO requires trusted HTTPS URLs");
  }
  if (origin.origin !== process.env.APP_ORIGIN) throw new Error("APP_ORIGIN must be an origin without a path");
  if ((process.env.AUTH_SESSION_SECRET || "").length < 32 || /change[_-]?me/i.test(process.env.AUTH_SESSION_SECRET || "")) throw new Error("Session encryption is not configured");
  return { origin: origin.origin, issuer, clientId, redirectUri: new URL("/api/auth/callback", origin).href };
}

let cached: { key: string; expires: number; config: oidc.Configuration } | undefined;

export async function oidcConfiguration() {
  const settings = oidcSettings();
  const secret = process.env.OIDC_CLIENT_SECRET;
  const key = JSON.stringify([settings.issuer.href, settings.clientId, secret]);
  if (cached?.key === key && cached.expires > Date.now()) return cached.config;
  const config = await oidc.discovery(settings.issuer, settings.clientId, secret || undefined, undefined, {
    timeout: 5,
    ...(settings.issuer.protocol === "http:" ? { execute: [oidc.allowInsecureRequests] } : {}),
  });
  cached = { key, expires: Date.now() + 600_000, config };
  return config;
}

export async function verifyAdminAccessToken(accessToken: string): Promise<string> {
  const backend = (process.env.API_INTERNAL_URL || "http://localhost:8000").replace(/\/$/, "");
  const response = await fetch(`${backend}/api/admin/identity`, {
    headers: { Authorization: `Bearer ${accessToken}` }, cache: "no-store", signal: AbortSignal.timeout(5_000),
  });
  if (!response.ok) throw new Error("The identity is not authorized as an administrator");
  const payload = await response.json() as { actor?: unknown };
  if (typeof payload.actor !== "string" || !payload.actor) throw new Error("Administrator identity is missing");
  return payload.actor;
}
