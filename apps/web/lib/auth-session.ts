import "server-only";
import { createHash, randomBytes } from "node:crypto";
import { createClient } from "redis";
import { NextRequest } from "next/server";
import { openAuthValue, sealAuthValue, sameSecret } from "@/lib/auth-crypto";
import { sessionRedisOptions } from "@/lib/redis-connection";

export interface AdminSession {
  accessToken: string;
  actor: string;
  expiresAt: number;
  csrfToken: string;
}

export const sessionCookieName = process.env.NODE_ENV === "production" ? "__Host-heatshift-admin" : "heatshift-admin";
export const loginCookieName = process.env.NODE_ENV === "production" ? "__Host-heatshift-login" : "heatshift-login";
export const authCookieOptions = { httpOnly: true, secure: process.env.NODE_ENV === "production", sameSite: "lax" as const, path: "/" };

let redisClient: ReturnType<typeof createClient> | undefined;
let connecting: Promise<ReturnType<typeof createClient>> | undefined;

export async function closeAdminSessionStore() {
  if (connecting) await connecting.catch(() => undefined);
  if (redisClient?.isOpen) await redisClient.close();
  redisClient = undefined;
}

async function store() {
  if (!redisClient?.isReady) {
    if (!connecting) {
      const client = createClient(sessionRedisOptions(process.env.AUTH_REDIS_URL || process.env.REDIS_URL || "redis://localhost:6379/2"));
      client.on("error", () => { /* Fail closed; never log connection credentials or tokens. */ });
      connecting = client.connect().then(() => { redisClient = client; return client; }).finally(() => { connecting = undefined; });
    }
    await connecting;
  }
  return redisClient!.withCommandOptions({ timeout: 1500 });
}

function sessionKey(id: string) {
  return `heatshift:admin-session:${createHash("sha256").update(id).digest("hex")}`;
}

export async function createAdminSession(accessToken: string, actor: string, ttlSeconds: number) {
  const ttl = Math.min(900, Math.floor(ttlSeconds));
  if (ttl < 1 || !accessToken || accessToken.length > 16_384) throw new Error("Invalid or expired access token");
  const id = randomBytes(32).toString("base64url");
  const session: AdminSession = { accessToken, actor, expiresAt: Date.now() + ttl * 1000, csrfToken: randomBytes(32).toString("base64url") };
  await (await store()).set(sessionKey(id), sealAuthValue(session), { EX: ttl });
  return { id, ttl, session };
}

export async function readAdminSession(request: NextRequest): Promise<AdminSession | null> {
  const id = request.cookies.get(sessionCookieName)?.value;
  if (!id || !/^[\w-]{43}$/.test(id)) return null;
  const raw = await (await store()).get(sessionKey(id));
  const session = raw ? openAuthValue<AdminSession>(raw) : null;
  return session && typeof session.expiresAt === "number" && session.expiresAt > Date.now()
    && typeof session.accessToken === "string" && typeof session.csrfToken === "string" && typeof session.actor === "string" ? session : null;
}

export async function deleteAdminSession(request: NextRequest) {
  const id = request.cookies.get(sessionCookieName)?.value;
  if (id && /^[\w-]{43}$/.test(id)) await (await store()).del(sessionKey(id));
}

export function validSessionMutation(request: NextRequest, session: AdminSession): boolean {
  return Boolean(process.env.APP_ORIGIN) && request.headers.get("origin") === process.env.APP_ORIGIN
    && sameSecret(request.headers.get("x-csrf-token") || "", session.csrfToken);
}
