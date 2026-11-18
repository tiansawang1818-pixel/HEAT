import { describe, expect, it, vi } from "vitest";
import { GET } from "@/app/api/health/live/route";

describe("liveness probe", () => {
  it("answers from the web server without calling the API", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const response = GET();
    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    expect((await response.json()).status).toBe("ok");
    expect(fetchMock).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});
