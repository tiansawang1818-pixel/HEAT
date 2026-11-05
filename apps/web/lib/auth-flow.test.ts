import { generateKeyPairSync, sign } from "node:crypto";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const memory = vi.hoisted(() => {
  const rows = new Map<string, string>();
  const client = { isReady: true, on: vi.fn(), connect: vi.fn(), withCommandOptions: vi.fn(),
    set: vi.fn(async (key: string, value: string) => { rows.set(key, value); }),
    get: vi.fn(async (key: string) => rows.get(key) ?? null),
    del: vi.fn(async (key: string) => rows.delete(key)),
  };
  client.connect.mockImplementation(async () => client);
  client.withCommandOptions.mockImplementation(() => client);
  return { rows, client };
});

vi.mock("server-only", () => ({}));
vi.mock("redis", () => ({ createClient: () => memory.client }));

import { sealAuthValue, openAuthValue } from "./auth-crypto";
import { loginCookieName, sessionCookieName, readAdminSession } from "./auth-session";
import { oidcSettings } from "./oidc";
import { proxyAdminPost } from "./admin-proxy";
import { GET as beginLogin } from "@/app/api/auth/login/route";
import { GET as callback } from "@/app/api/auth/callback/route";
import { POST as logout } from "@/app/api/auth/logout/route";

const { publicKey, privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
let nonce = "";
let verifier = "";
let adminAllowed = true;

function idToken() {
  const now = Math.floor(Date.now() / 1000);
  const header = Buffer.from(JSON.stringify({ alg: "RS256", kid: "ci-identity-key" })).toString("base64url");
  const payload = Buffer.from(JSON.stringify({ iss: "https://identity.example.org", aud: "heatshift-test", sub: "admin-1", nonce, iat: now, exp: now + 300 })).toString("base64url");
  const body = `${header}.${payload}`;
  return `${body}.${sign("sha256", Buffer.from(body), privateKey).toString("base64url")}`;
}

beforeEach(() => {
  memory.rows.clear(); adminAllowed = true;
  vi.stubEnv("ADMIN_AUTH_MODE", "oidc");
  vi.stubEnv("APP_ORIGIN", "https://heat.example.org");
  vi.stubEnv("OIDC_ISSUER", "https://identity.example.org");
  vi.stubEnv("OIDC_CLIENT_ID", "heatshift-test");
  vi.stubEnv("AUTH_SESSION_SECRET", "isolated-unit-test-encryption-key-of-sufficient-length");
  vi.stubEnv("API_INTERNAL_URL", "https://backend.example.org");
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    if (url.pathname.includes(".well-known")) return Response.json({
      issuer: "https://identity.example.org", authorization_endpoint: "https://identity.example.org/authorize",
      token_endpoint: "https://identity.example.org/token", jwks_uri: "https://identity.example.org/jwks",
      response_types_supported: ["code"], subject_types_supported: ["public"], id_token_signing_alg_values_supported: ["RS256"],
      code_challenge_methods_supported: ["S256"], token_endpoint_auth_methods_supported: ["none"],
    });
    if (url.pathname === "/token") {
      const form = new URLSearchParams(await request.text());
      expect(form.get("code_verifier")).toBe(verifier);
      expect(form.get("redirect_uri")).toBe("https://heat.example.org/api/auth/callback");
      return Response.json({ access_token: "issued-access-token-stays-on-server", token_type: "Bearer", expires_in: 300, id_token: idToken() });
    }
    if (url.pathname === "/jwks") return Response.json({ keys: [{ ...publicKey.export({ format: "jwk" }), kid: "ci-identity-key", alg: "RS256", use: "sig" }] });
    if (url.pathname === "/api/admin/identity") return Response.json(adminAllowed ? { actor: "verified-admin" } : { detail: "admin role required" }, { status: adminAllowed ? 200 : 403 });
    return Response.json({ ok: true });
  }));
});

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

async function login() {
  const redirect = await beginLogin();
  expect(redirect.status).toBe(307);
  const loginCookie = redirect.cookies.get(loginCookieName)!.value;
  const payload = openAuthValue<{ verifier: string; nonce: string; state: string }>(loginCookie)!;
  nonce = payload.nonce; verifier = payload.verifier;
  const target = new URL(redirect.headers.get("location")!);
  expect(target.searchParams.get("code_challenge_method")).toBe("S256");
  expect(target.searchParams.get("nonce")).toBe(nonce);
  return { loginCookie, state: payload.state };
}

describe("server-side administrator login", () => {
  it("completes real OIDC code/PKCE/nonce validation and encrypts the token in server storage", async () => {
    const { loginCookie, state } = await login();
    const response = await callback(new NextRequest(`https://heat.example.org/api/auth/callback?code=test-code&state=${state}`, { headers: { cookie: `${loginCookieName}=${loginCookie}` } }));
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe("https://heat.example.org/observatory");
    const id = response.cookies.get(sessionCookieName)!.value;
    expect(id).not.toContain("issued-access-token");
    expect(response.headers.get("set-cookie")).toContain("HttpOnly");
    expect([...memory.rows.values()][0]).not.toContain("issued-access-token");
    const request = new NextRequest("https://heat.example.org/api/admin/ingestion", { method: "POST", headers: { cookie: `${sessionCookieName}=${id}` } });
    const session = await readAdminSession(request);
    expect(session?.actor).toBe("verified-admin");
    expect((await proxyAdminPost(request, "/api/ingestion/run")).status).toBe(403);
    const headers = { cookie: `${sessionCookieName}=${id}`, origin: "https://heat.example.org", "X-CSRF-Token": session!.csrfToken };
    const allowed = new NextRequest(request.url, { method: "POST", headers, body: JSON.stringify({ source: "open-meteo" }) });
    expect((await proxyAdminPost(allowed, "/api/ingestion/run")).status).toBe(200);
    const oversized = new NextRequest(request.url, { method: "POST", headers, body: JSON.stringify({ source: "open-meteo", extra: "ก".repeat(12_000) }) });
    expect((await proxyAdminPost(oversized, "/api/ingestion/run")).status).toBe(413);
    const crossOrigin = new NextRequest(request.url, { method: "POST", headers: { ...headers, origin: "https://untrusted.example.org" } });
    expect((await logout(crossOrigin)).status).toBe(403);
    expect((await logout(new NextRequest(request.url, { method: "POST", headers }))).status).toBe(200);
    expect(await readAdminSession(request)).toBeNull();
  });

  it("rejects mismatched OAuth state and does not create an administrator session", async () => {
    const { loginCookie } = await login();
    const response = await callback(new NextRequest("https://heat.example.org/api/auth/callback?code=test-code&state=wrong-state", { headers: { cookie: `${loginCookieName}=${loginCookie}` } }));
    expect(response.status).toBe(401);
    expect(memory.rows.size).toBe(0);
  });

  it("rejects an authenticated account without a verified backend administrator role", async () => {
    adminAllowed = false;
    const { loginCookie, state } = await login();
    const response = await callback(new NextRequest(`https://heat.example.org/api/auth/callback?code=test-code&state=${state}`, { headers: { cookie: `${loginCookieName}=${loginCookie}` } }));
    expect(response.status).toBe(401);
    expect(memory.rows.size).toBe(0);
  });

  it("rejects tampered encrypted login values and weak session configuration", () => {
    const encrypted = sealAuthValue({ expiresAt: Date.now() + 1000 });
    expect(openAuthValue(`${encrypted.slice(0, -4)}AAAA`)).toBeNull();
    vi.stubEnv("AUTH_SESSION_SECRET", "short");
    expect(() => oidcSettings()).toThrow();
    expect(openAuthValue(encrypted)).toBeNull();
  });
});
