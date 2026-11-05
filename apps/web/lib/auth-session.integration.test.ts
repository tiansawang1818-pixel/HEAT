import { createHash } from "node:crypto";
import { setTimeout } from "node:timers/promises";
import { NextRequest } from "next/server";
import { createClient } from "redis";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
import { closeAdminSessionStore, createAdminSession, deleteAdminSession, readAdminSession, sessionCookieName } from "./auth-session";
import { GET as sessionStatus } from "@/app/api/auth/session/route";

const enabled = process.env.HEATSHIFT_RUN_SESSION_TESTS === "true";
const redisUrl = process.env.AUTH_REDIS_URL || "redis://127.0.0.1:6379/2";
let client: ReturnType<typeof createClient> | undefined;
const ids: string[] = [];
const key = (id: string) => `heatshift:admin-session:${createHash("sha256").update(id).digest("hex")}`;
const request = (id: string) => new NextRequest("https://heat.example.org/api/auth/session", { headers: { cookie: `${sessionCookieName}=${id}` } });

describe.runIf(enabled)("administrator session with real Redis", () => {
  beforeEach(async () => {
    const url = new URL(redisUrl);
    if (!["127.0.0.1", "localhost"].includes(url.hostname) || url.pathname !== "/2" || process.env.CI !== "true") {
      throw new Error("Session integration requires an isolated localhost CI Redis DB 2");
    }
    vi.stubEnv("AUTH_REDIS_URL", redisUrl);
    vi.stubEnv("AUTH_SESSION_SECRET", "isolated-ci-session-encryption-key-not-for-production");
    vi.stubEnv("ADMIN_AUTH_MODE", "oidc");
    vi.stubEnv("APP_ORIGIN", "https://heat.example.org");
    vi.stubEnv("OIDC_ISSUER", "https://identity.example.org");
    vi.stubEnv("OIDC_CLIENT_ID", "ci-client");
    client = createClient({ url: redisUrl });
    await client.connect();
  });

  afterEach(async () => {
    await closeAdminSessionStore();
    if (client?.isOpen) {
      for (const id of ids.splice(0)) await client.del(key(id));
      await client.close();
    }
    vi.unstubAllEnvs();
  });

  it("stores encrypted credentials with a bounded TTL, returns no access token and deletes on logout", async () => {
    const created = await createAdminSession("real-redis-test-access-token", "ci-admin", 3600);
    ids.push(created.id);
    expect(await client!.ttl(key(created.id))).toBeGreaterThan(0);
    expect(await client!.ttl(key(created.id))).toBeLessThanOrEqual(900);
    const stored = await client!.get(key(created.id));
    expect(stored).toBeTruthy();
    expect(stored).not.toContain("real-redis-test-access-token");
    expect(await readAdminSession(request(created.id))).toMatchObject({ actor: "ci-admin" });
    const response = await sessionStatus(request(created.id));
    const payload = await response.json();
    expect(payload.authenticated).toBe(true);
    expect(JSON.stringify(payload)).not.toContain("real-redis-test-access-token");
    await deleteAdminSession(request(created.id));
    expect(await client!.exists(key(created.id))).toBe(0);
    expect(await readAdminSession(request(created.id))).toBeNull();
  });

  it("expires the stored session automatically", async () => {
    const created = await createAdminSession("short-lived-ci-token", "ci-admin", 1);
    ids.push(created.id);
    expect(await readAdminSession(request(created.id))).not.toBeNull();
    await setTimeout(1200);
    expect(await client!.exists(key(created.id))).toBe(0);
    expect(await readAdminSession(request(created.id))).toBeNull();
  });

  it("fails closed when the Redis session service is unavailable", async () => {
    const created = await createAdminSession("ci-token", "ci-admin", 60);
    ids.push(created.id);
    await closeAdminSessionStore();
    vi.stubEnv("AUTH_REDIS_URL", "redis://127.0.0.1:1/2");
    const response = await sessionStatus(request(created.id));
    expect(response.status).toBe(503);
    expect((await response.json()).authenticated).toBe(false);
  });
});
