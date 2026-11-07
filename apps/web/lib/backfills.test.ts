import { afterEach, expect, it, vi } from "vitest";
import { controlBackfill, loadBackfills, startBackfill } from "./backfills";

afterEach(() => { vi.unstubAllGlobals(); });

it("uses real job APIs, forwards bounded definitions and keeps operator credentials out of payloads", async () => {
  const fetch = vi.fn(async () => Response.json({ job_id: "fixture-job", status: "queued" }));
  vi.stubGlobal("fetch", fetch);
  await startBackfill({ source: "landsat", start_date: "2020-01-01", end_date: "2025-01-01",
    max_assets: 2, max_catalogue_requests: 4, step_delay_seconds: 600, page_size: 5, window_days: 16 }, "unit-only-token");
  const [path, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
  expect(path).toBe("/api/admin/backfills");
  expect(JSON.parse(init.body as string)).toMatchObject({ max_assets: 2, max_catalogue_requests: 4 });
  expect(init.headers).toMatchObject({ "X-Local-Admin-Token": "unit-only-token" });
  expect(init.body).not.toContain("unit-only-token");
});

it("uses a renewed CSRF token for SSO controls and does not send new date ranges on resume", async () => {
  const fetch = vi.fn(async (url: string) => Response.json(url === "/api/auth/session"
    ? { authenticated: true, csrf_token: "csrf-unit-only" } : { status: "queued" }));
  vi.stubGlobal("fetch", fetch);
  await controlBackfill("fixture-job", "resume", { max_assets: 1, max_catalogue_requests: 2, step_delay_seconds: 600 }, "__session__");
  const [path, init] = fetch.mock.calls[1] as unknown as [string, RequestInit];
  expect(path).toBe("/api/admin/backfills/fixture-job/resume");
  expect(init.headers).toMatchObject({ "X-CSRF-Token": "csrf-unit-only" });
  expect(init.body).not.toContain("start_date");
});

it("honors cancellation and surfaces provider/queue failures without pretending success", async () => {
  const fetch = vi.fn(async () => Response.json({ detail: "คิวงานถาวรยังไม่พร้อม" }, { status: 503 }));
  vi.stubGlobal("fetch", fetch);
  await expect(controlBackfill("fixture-job", "cancel", { max_assets: 1, max_catalogue_requests: 1, step_delay_seconds: 60 }, "unit-only-token"))
    .rejects.toThrow("คิวงานถาวรยังไม่พร้อม");
  const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
  expect(init.body).toBe("{}");
  vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("offline"); }));
  await expect(loadBackfills()).rejects.toThrow("offline");
});
