import { describe, expect, it } from "vitest";
import { sessionRedisOptions } from "./redis-connection";

describe("administrator Redis transport", () => {
  it("uses the managed hostname as SNI and keeps certificate checks enabled", () => {
    const options = sessionRedisOptions("rediss://default:test-only@db.example.org:6380/2");
    expect(options.socket).toMatchObject({ tls: true, servername: "db.example.org", rejectUnauthorized: true, reconnectStrategy: false });
    expect(options.disableOfflineQueue).toBe(true);
  });

  it("retains the private Compose connection without TLS options", () => {
    const options = sessionRedisOptions("redis://redis:6379/2");
    expect(options.socket).not.toHaveProperty("tls");
    expect(options.socket).not.toHaveProperty("servername");
  });

  it("rejects unrelated connection protocols without exposing the URL", () => {
    expect(() => sessionRedisOptions("https://secret@db.example.org")).toThrow("Unsupported Redis connection protocol");
  });
});
