import { describe, expect, it } from "vitest";
import { formatBaht, formatNumber, freshnessLabel } from "./utils";

describe("Thai presentation helpers", () => {
  it("formats a million-baht budget compactly", () => {
    expect(formatBaht(5_000_000)).toContain("5");
    expect(formatBaht(5_000_000)).toContain("ล้าน");
  });

  it("does not invent a number for missing values", () => {
    expect(formatNumber(null)).toBe("—");
  });

  it("translates freshness states", () => {
    expect(freshnessLabel("stale")).toBe("ข้อมูลเก่า");
  });
});
