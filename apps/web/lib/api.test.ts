import { afterEach, describe, expect, it, vi } from "vitest";
import { askAssistant, compareScenarios, loadDashboardData, loadDatasetVersions, runForecastScenario, saveScenario } from "./api";
import { formatBaht, formatNumber, freshnessLabel } from "./utils";

afterEach(() => vi.unstubAllGlobals());

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

  it("keeps scenario allocation geometry for map comparison", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      input_versions: { worldpop: { version_id: "v1", layer_version: 1 } },
      scenarios: [{
        strategy: "heatshift_optimized",
        label_th: "HEAT//SHIFT สมดุล",
        allocated_thb: 400_000,
        estimated_beneficiaries: 750,
        objective_value: 0.8,
        allocations: [{
          id: "candidate-1",
          name_th: "จุดปลูกต้นไม้",
          intervention_type: "tree_planting",
          estimated_cost_thb: 400_000,
          estimated_beneficiaries: 750,
          priority_score: 81,
          geometry: { type: "Point", coordinates: [100.51, 13.75] },
        }],
      }],
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    const [scenario] = await compareScenarios({ budget_thb: 1_000_000, intervention_types: ["tree_planting"] });
    expect(scenario.run_id).toBe("scenario:heatshift_optimized");
    expect(scenario.allocations[0]).toMatchObject({ longitude: 100.51, latitude: 13.75, cost_thb: 400_000 });
  });

  it("sends an explicit supported forecast horizon", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => new Response(JSON.stringify({
      run_id: "forecast-1",
      status: "insufficient_data",
      mode: "scenario_only",
      projections: [],
      method: "guardrail-insufficient-data",
      model_version: "scenario-v1",
      training_versions: {},
      assumptions: {},
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await runForecastScenario(3);
    expect(result.mode).toBe("scenario_only");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ horizon_years: 3, area_ids: null });
  });

  it("loads validated model weather context without treating it as satellite LST", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.includes("/api/datasets/status")) return Response.json({ items: [] });
      if (url.includes("/api/map/layers")) return Response.json({ items: [{
        id: "priority",
        name_th: "ลำดับความสำคัญการรับความร้อน",
        status: "unavailable",
        source_name: "HEAT//SHIFT derived score",
        source_status: {},
      }] });
      if (url.includes("/api/map/features")) return Response.json({ type: "FeatureCollection", features: [] });
      if (url.includes("/api/weather/context")) return Response.json({
        status: "up_to_date",
        source_name: "Open-Meteo",
        dataset_id: "open-meteo/weather-context",
        data_class: "model_estimate_and_forecast",
        reference_time: "2026-10-31T04:00:00+00:00",
        layer_version: 3,
        current: { temperature_2m: 31.2, apparent_temperature: 35.4, relative_humidity_2m: 63, wind_speed_10m: 7.1 },
        current_units: { temperature_2m: "°C" },
        hourly: { time: ["2026-10-31T05:00"], temperature_2m: [31.8], precipitation_probability: [20] },
        hourly_units: { temperature_2m: "°C", precipitation_probability: "%" },
        limitations_th: "แบบจำลองและพยากรณ์ ไม่ใช่ LST ดาวเทียม",
      });
      return new Response(null, { status: 404 });
    }));

    const dashboard = await loadDashboardData();
    expect(dashboard.weather?.current?.temperature_2m).toBe(31.2);
    expect(dashboard.weather?.data_class).toBe("model_estimate_and_forecast");
    expect(dashboard.weather?.limitations_th).toContain("ไม่ใช่ LST");
    expect(dashboard.layers[0]).toMatchObject({ id: "priority", layer_version: 0, observation_date: null });
    expect(dashboard.isDemo).toBe(false);
  });

  it("normalizes a grounded assistant optimization for immediate map display", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({
      answer_th: "คำนวณจาก candidate ที่มีหลักฐานแล้ว",
      grounding: [{ type: "optimization_run", id: "run-1" }],
      mode: "deterministic",
      optimization: {
        run_id: "run-1",
        budget_thb: 5_000_000,
        allocated_thb: 2_000_000,
        estimated_beneficiaries: 1_200,
        objective_value: 0.8,
        input_versions: { worldpop: { version_id: "v1" } },
        selected: [{
          id: "assistant-tree",
          name_th: "แนวต้นไม้ที่ผ่านการสำรวจ",
          intervention_type: "tree_planting",
          estimated_cost_thb: 2_000_000,
          estimated_beneficiaries: 1_200,
          priority_score: 86,
          geometry: { type: "Point", coordinates: [100.52, 13.74] },
        }],
      },
    })));

    const result = await askAssistant("มีงบ 5 ล้านบาท ควรลงทุนตรงไหน?");
    expect(result.optimization?.run_id).toBe("run-1");
    expect(result.optimization?.allocations[0]).toMatchObject({ longitude: 100.52, latitude: 13.74 });
    expect(result.grounding[0].type).toBe("optimization_run");
  });

  it("saves a versioned scenario and uses the server-recomputed comparison", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => Response.json({
      id: "saved-1",
      name: "แผนงบ 5 ล้านบาท",
      created_at: "2026-11-04T02:00:00+00:00",
      is_outdated: false,
      comparison: {
        budget_thb: 5_000_000,
        input_versions: { worldpop: { version_id: "v2" } },
        scenarios: [{
          strategy: "heatshift_optimized",
          label_th: "HEAT//SHIFT สมดุล",
          allocated_thb: 3_000_000,
          estimated_beneficiaries: 1_800,
          objective_value: 0.9,
          allocations: [{
            id: "shade-1",
            name_th: "โครงสร้างร่มเงา",
            intervention_type: "shade_structure",
            estimated_cost_thb: 3_000_000,
            estimated_beneficiaries: 1_800,
            priority_score: 82,
            geometry: { type: "Point", coordinates: [100.55, 13.76] },
          }],
        }],
      },
    }));
    vi.stubGlobal("fetch", fetchMock);

    const saved = await saveScenario({
      name: "แผนงบ 5 ล้านบาท",
      budget_thb: 5_000_000,
      intervention_types: ["shade_structure"],
    });
    expect(saved.id).toBe("saved-1");
    expect(saved.scenarios[0].dataset_versions).toEqual({ worldpop: { version_id: "v2" } });
    expect(saved.scenarios[0].allocations[0]).toMatchObject({ longitude: 100.55, latitude: 13.76 });
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({
      name: "แผนงบ 5 ล้านบาท",
      budget_thb: 5_000_000,
      enabled_types: ["shade_structure"],
    });
  });

  it("loads validation provenance without requiring raw provider payloads", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({
      source: { id: "source-1", source_name: "Open-Meteo", provider: "Open-Meteo", dataset_id: "open-meteo/weather-context" },
      items: [{
        id: "version-1",
        source_asset_id: "weather:2026-11-04T02:00Z",
        source_version: "forecast-api-v1",
        status: "failed",
        quality_status: "rejected",
        layer_version: 0,
        provider_payload_preserved: true,
        validation: { passed: false, checks: { forecast_hours: 0 }, warnings: [], errors: ["missing hourly fields"] },
      }],
      page: 1,
      page_size: 20,
      total: 1,
    })));

    const history = await loadDatasetVersions("source-1");
    expect(history.items[0].validation?.passed).toBe(false);
    expect(history.items[0].validation?.errors[0]).toContain("hourly");
    expect(history.items[0].provider_payload_preserved).toBe(true);
  });
});
