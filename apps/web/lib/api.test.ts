import { afterEach, describe, expect, it, vi } from "vitest";
import { askAssistant, compareScenarios, loadAreaDetail, loadDashboardData, loadDatasetVersions, loadLayerFeatures, loadProductionReadiness, loadPublishedDataReadiness, loadRasterTileDescriptor, runForecastScenario, saveScenario, searchAreaDirectory } from "./api";
import { formatBaht, formatNumber, freshnessLabel } from "./utils";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("Thai presentation helpers", () => {
  it("keeps population counts, reference years, environmental observations and score calculation times distinct", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({
      id: "unit-area", indicators: { population_density: { value: 500, observed_at: "2026-01-01T00:00:00Z" } },
      priority: { calculated_at: "2026-11-06T00:00:00Z" },
    })));
    const missing = await loadAreaDetail("unit-area");
    expect(missing.population).toBeUndefined();
    expect(missing.observation_date).toBeNull();
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({
      id: "unit-area", indicators: {
        population_estimate: { value: 1000, observed_at: "2026-01-01T00:00:00Z", quality_status: "model_estimate" },
        population_reference_year: { value: 2026, observed_at: "2026-01-01T00:00:00Z" },
        ndvi: { value: .5, observed_at: "2025-12-30T00:00:00Z" },
      }, priority: { calculated_at: "2026-11-06T00:00:00Z" },
    })));
    expect(await loadAreaDetail("unit-area")).toMatchObject({ population: 1000, population_reference_year: 2026, observation_date: "2025-12-30T00:00:00Z" });
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ type: "FeatureCollection", features: [{
      type: "Feature", geometry: { type: "Point", coordinates: [100.5,13.7] }, properties: { id: "unit-area", population: 1000, value: 500 },
    }] })));
    expect((await loadLayerFeatures("population")).features[0].properties).toMatchObject({ population: 1000, population_density: 500 });
  });

  it("does not mistake configuration readiness or contradictory results for validated decision data", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ scope: "configuration_and_dependencies", configuration_ready: true })));
    await expect(loadPublishedDataReadiness()).rejects.toThrow("รูปแบบผลตรวจข้อมูล");
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({
      scope: "published_data_and_recorded_decision_evidence", data_ready: true,
      checks: [{ code: "source.landsat", status: "blocked" }], coverage: {},
      summary: { ready: 0, warning: 0, blocked: 1 }, limitations_th: "unit test", external_verification_required_th: [],
    })));
    await expect(loadPublishedDataReadiness()).rejects.toThrow("รูปแบบผลตรวจข้อมูล");
  });

  it("formats a million-baht budget compactly", () => {
    expect(formatBaht(5_000_000)).toContain("5");
    expect(formatBaht(5_000_000)).toContain("ล้าน");
  });

  it("does not invent a number for missing values", () => {
    expect(formatNumber(null)).toBe("—");
  });

  it.each(["false", "true"])("honors NEXT_PUBLIC_DEMO_MODE=%s when the API is unavailable", async (mode) => {
    vi.stubEnv("NEXT_PUBLIC_DEMO_MODE", mode);
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("API offline"); }));
    const dashboard = await loadDashboardData();
    expect(dashboard.isDemo).toBe(mode === "true");
    if (mode === "false") expect(dashboard.features.features).toHaveLength(0);
    else {
      expect(dashboard.features.features.length).toBeGreaterThan(0);
      expect(dashboard.features.features.every((feature) => feature.properties.quality_status === "demo_only" && feature.properties.surface_temperature_c == null)).toBe(true);
    }
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
      metrics: { validation_passed: false },
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

  it("refreshes dashboard metadata after an event without redownloading map features", async () => {
    const requested: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      requested.push(url);
      if (url.includes("/api/datasets/status")) return Response.json({ items: [] });
      if (url.includes("/api/map/layers")) return Response.json({ items: [] });
      if (url.includes("/api/weather/context")) return Response.json({ status: "unavailable" });
      return new Response(null, { status: 404 });
    }));

    const dashboard = await loadDashboardData(false);
    expect(requested.some((url) => url.includes("/api/map/features"))).toBe(false);
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

  it("loads only a validated provider-backed raster descriptor", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({
      available: true,
      layer: "temperature",
      tiles: ["https://earthengine.googleapis.com/v1/projects/demo/maps/map-1/tiles/{z}/{x}/{y}"],
      tile_size: 256,
      min_zoom: 8,
      max_zoom: 18,
      bounds: [100.3, 13.45, 100.95, 14.05],
      attribution: "USGS Landsat / Google Earth Engine",
      dataset_version_id: "version-1",
      source_asset_id: "LANDSAT/LC09/C02/T1_L2/scene-1",
      source_version: "scene-1",
      layer_version: 4,
      observed_at: "2026-11-01T03:00:00+00:00",
      quality_status: "validated",
      legend: { min: 20, max: 50, palette: ["313695", "a50026"], unit: "°C" },
      delivery: "google-earth-engine-provider-xyz",
    })));

    const descriptor = await loadRasterTileDescriptor("temperature");
    expect(descriptor).toMatchObject({ layer: "temperature", layer_version: 4 });
    expect(descriptor?.tiles[0]).toContain("earthengine.googleapis.com");
  });

  it("keeps district GeoJSON as the fallback when a raster is unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({
      available: false,
      layer: "ndvi",
      reason: "No validated published raster version",
    })));

    await expect(loadRasterTileDescriptor("ndvi")).resolves.toBeNull();
  });

  it("loads a secret-free production readiness audit with live dependency probes", async () => {
    const fetchMock = vi.fn(async (_url: string) => Response.json({
      status: "not_ready",
      configuration_ready: false,
      scope: "configuration_and_dependencies",
      limitations_th: "ต้องยืนยันการ ingest จริงก่อนเปิดใช้งาน",
      environment: "development",
      generated_at: "2026-11-05T08:00:00+00:00",
      summary: { ready: 3, warning: 2, blocked: 6 },
      checks: [{ code: "storage.postgis", category: "storage", status: "blocked", label_th: "PostgreSQL / PostGIS", detail_th: "ใช้ sqlite" }],
      blockers: [],
      warnings: [],
      secrets_exposed: false,
    }));
    vi.stubGlobal("fetch", fetchMock);

    const readiness = await loadProductionReadiness();
    expect(readiness.configuration_ready).toBe(false);
    expect(readiness.secrets_exposed).toBe(false);
    expect(String(fetchMock.mock.calls[0][0])).toContain("probe_dependencies=true");
  });

  it("keeps a synthetic observation labelled even when its boundary is official", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({
      id: "official-area", name_th: "เขตทดสอบ", boundary_provenance: { quality_status: "verified_official_boundary" },
      indicators: { lst_c_mean: { value: 38.4, observed_at: "2026-11-05T03:00:00Z", quality_status: "synthetic_demo" } },
      priority: null,
    })));
    const area = await loadAreaDetail("official-area");
    expect(area.quality_status).toBe("synthetic_demo");
    expect(area.surface_temperature_c).toBe(38.4);
  });

  it("loads only the active viewport and carries the requested layer version", async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => Response.json({ type: "FeatureCollection", features: [] }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await loadLayerFeatures("temperature", [100.401234, 13.612345, 100.589876, 13.834567], controller.signal, 7, 9);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("layer=temperature");
    expect(String(url)).toContain("bbox=100.40123,13.61234,100.58988,13.83457");
    expect(String(url)).toContain("layer_version=7");
    expect(String(url)).toContain("refresh_version=9");
    expect(init?.signal).toBe(controller.signal);
    expect(init?.cache).toBe("force-cache");
  });

  it("searches the lightweight area directory without downloading boundaries", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({
      items: [{
        id: "bma-khlong-toei",
        name_th: "คลองเตย",
        name_en: "Khlong Toei",
        area_type: "district",
        centroid: { type: "Point", coordinates: [100.57, 13.71] },
      }],
    })));

    const [area] = await searchAreaDirectory("คลองเตย");
    expect(area).toMatchObject({ id: "bma-khlong-toei", name_en: "Khlong Toei" });
    expect(area.centroid).toEqual({ type: "Point", coordinates: [100.57, 13.71] });
  });
});
