import type { RedisClientOptions } from "redis";

export function sessionRedisOptions(url: string): RedisClientOptions {
  const parsed = new URL(url);
  if (parsed.protocol !== "redis:" && parsed.protocol !== "rediss:") {
    throw new Error("Unsupported Redis connection protocol");
  }
  const common = { connectTimeout: 1500, reconnectStrategy: false as const };
  return {
    url,
    socket: parsed.protocol === "rediss:"
      ? { ...common, tls: true, servername: parsed.hostname, rejectUnauthorized: true }
      : common,
    disableOfflineQueue: true,
  };
}
