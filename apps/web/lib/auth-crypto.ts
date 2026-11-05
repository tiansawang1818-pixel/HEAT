import { createCipheriv, createDecipheriv, createHash, randomBytes, timingSafeEqual } from "node:crypto";

function key() {
  const secret = process.env.AUTH_SESSION_SECRET || "";
  if (secret.length < 32 || /change[_-]?me/i.test(secret)) throw new Error("AUTH_SESSION_SECRET must be a random secret of at least 32 characters");
  return createHash("sha256").update(secret).digest();
}

export function sealAuthValue(value: object): string {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", key(), iv);
  const encrypted = Buffer.concat([cipher.update(JSON.stringify(value), "utf8"), cipher.final()]);
  return Buffer.concat([iv, cipher.getAuthTag(), encrypted]).toString("base64url");
}

export function openAuthValue<T>(value: string): T | null {
  try {
    const raw = Buffer.from(value, "base64url");
    if (raw.length < 29 || raw.length > 40_000) return null;
    const decipher = createDecipheriv("aes-256-gcm", key(), raw.subarray(0, 12));
    decipher.setAuthTag(raw.subarray(12, 28));
    return JSON.parse(Buffer.concat([decipher.update(raw.subarray(28)), decipher.final()]).toString("utf8")) as T;
  } catch {
    return null;
  }
}

export function sameSecret(left: string, right: string): boolean {
  const a = Buffer.from(left), b = Buffer.from(right);
  return a.length > 0 && a.length === b.length && timingSafeEqual(a, b);
}
