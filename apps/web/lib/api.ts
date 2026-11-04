import { defaultLayers, demoDatasets, demoFeatures } from "@/lib/demo-data";
import type {
  AreaProperties,
  DatasetStatus,
  FeatureCollection,
  ForecastResult,
  GeoFeature,
  HistoryPoint,
  MapLayerMeta,
  ObservatorySnapshot,
  OptimizationResult,
  PriorityRankingResult,
  WeatherContext,
} from "@/lib/utils";

const configuredBase = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "";

function endpoint(path: string) {
  return `${configuredBase}${path}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(endpoint(path), {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    const message = await response.text().catch(() => "");
    throw new Error(message || `API ${response.status}`);
  }
  return response.json() as Promise<T>;
}

function asItems<T>(value: unknown): T[] {
  if (Array.isArray(value)) return value as T[];
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    for (const key of ["items", "data", "datasets", "layers", "results"]) {
      if (Array.isArray(record[key])) return record[key] as T[];
    }
  }
  return [];
}

const layerProperty: Record<string, keyof AreaProperties> = {
  temperature: "surface_temperature_c",
  priority: "priority_score",
  vegetation: "tree_coverage_pct",
  ndvi: "ndvi",
  population: "population",
  built: "built_up_pct",
  "future-risk": "scenario_change_c",
};

function normalizeDataset(raw: Record<string, unknown>): DatasetStatus {
  return {
    source_name: String(raw.source_name || raw.provider || "ไม่ระบุแหล่งข้อมูล"),
    dataset_id: String(raw.dataset_id || raw.id || "unknown"),
    layer_name: typeof raw.layer_name === "string" ? raw.layer_name : undefined,
    status: (["up_to_date", "updating", "stale", "unavailable", "error"] as const).includes(raw.status as never)
      ? raw.status as DatasetStatus["status"] : "unavailable",
    last_source_observed_at: (raw.last_source_observed_at || raw.last_source_observation || null) as string | null,
    last_checked_at: (raw.last_checked_at || raw.last_system_check || null) as string | null,
    last_published_at: (raw.last_published_at || null) as string | null,
    next_check_at: (raw.next_check_at || null) as string | null,
    layer_version: typeof raw.layer_version === "number" ? raw.layer_version : 0,
    quality_status: typeof raw.quality_status === "string" ? raw.quality_status : undefined,
    status_reason: typeof raw.status_reason === "string" ? raw.status_reason : undefined,
  };
}

function normalizeFeatureCollection(value: FeatureCollection, layer: string): FeatureCollection {
  const propertyName = layerProperty[layer];
  return {
    type: "FeatureCollection",
    features: value.features
      .filter((feature) => feature?.type === "Feature" && feature.geometry)
      .map((feature) => {
        const source = feature.properties as Record<string, unknown>;
        const properties: AreaProperties & Record<string, unknown> = {
          ...source,
          id: String(source.id || source.area_id || feature.id || ""),
          name_th: String(source.name_th || "ไม่ทราบชื่อพื้นที่"),
          name_en: typeof source.name_en === "string" ? source.name_en : undefined,
          quality_status: typeof source.quality_status === "string" ? source.quality_status : undefined,
          observation_date: (source.observation_date || source.observed_at || null) as string | null,
        };
        if (propertyName && typeof source.value === "number") {
          (properties[propertyName] as number | null | undefined) = source.value;
        }
        return { ...feature, properties } as GeoFeature;
      }),
  };
}

function normalizeLayers(value: unknown, datasets: DatasetStatus[]): MapLayerMeta[] {
  const rawLayers = asItems<Record<string, unknown>>(value);
  if (!rawLayers.length) return defaultLayers;
  const statusOrder = ["error", "updating", "stale", "up_to_date", "unavailable"] as const;
  const fallbackStatus = statusOrder.find((status) => datasets.some((dataset) => dataset.status === status)) || "unavailable";
  return rawLayers.map((raw) => {
    const hasDeclaredSources = raw.source_status != null && typeof raw.source_status === "object";
    const sourceStatus = hasDeclaredSources
      ? Object.values(raw.source_status as Record<string, Record<string, unknown>>).map(normalizeDataset)
      : [];
    const relevant = hasDeclaredSources ? sourceStatus : datasets;
    const declaredStatus = typeof raw.status === "string" && statusOrder.includes(raw.status as typeof statusOrder[number])
      ? raw.status as typeof statusOrder[number]
      : null;
    const status = declaredStatus || statusOrder.find((candidate) => relevant.some((dataset) => dataset.status === candidate)) || fallbackStatus;
    const observations = relevant.map((dataset) => dataset.last_source_observed_at).filter(Boolean) as string[];
    const versions = relevant.map((dataset) => dataset.layer_version || 0);
    return {
      id: String(raw.id || "unknown"),
      name_th: String(raw.name_th || raw.id || "ชั้นข้อมูล"),
      description_th: typeof raw.description_th === "string" ? raw.description_th : undefined,
      unit: typeof raw.unit === "string" ? raw.unit : undefined,
      status,
      layer_version: Math.max(0, ...versions),
      observation_date: observations.sort().at(-1) || null,
      source_name: typeof raw.source_name === "string"
        ? raw.source_name
        : relevant.map((dataset) => dataset.source_name).slice(0, 2).join(" · ") || "ยังไม่ระบุ",
    };
  });
}

function normalizeWeather(value: unknown): WeatherContext | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  const current = raw.current && typeof raw.current === "object" ? raw.current as WeatherContext["current"] : null;
  const hourly = raw.hourly && typeof raw.hourly === "object" ? raw.hourly as WeatherContext["hourly"] : null;
  const allowed = ["up_to_date", "updating", "stale", "unavailable", "error"] as const;
  return {
    status: allowed.includes(raw.status as typeof allowed[number]) ? raw.status as WeatherContext["status"] : "unavailable",
    status_reason: typeof raw.status_reason === "string" ? raw.status_reason : undefined,
    source_name: String(raw.source_name || "Open-Meteo"),
    dataset_id: String(raw.dataset_id || "open-meteo/weather-context"),
    data_class: "model_estimate_and_forecast",
    reference_time: typeof raw.reference_time === "string" ? raw.reference_time : null,
    last_source_observation: typeof raw.last_source_observation === "string" ? raw.last_source_observation : null,
    last_system_check: typeof raw.last_system_check === "string" ? raw.last_system_check : null,
    provider_model: typeof raw.provider_model === "string" ? raw.provider_model : null,
    layer_version: typeof raw.layer_version === "number" ? raw.layer_version : 0,
    limitations_th: typeof raw.limitations_th === "string"
      ? raw.limitations_th
      : "ข้อมูลนี้เป็นค่าประมาณจากแบบจำลองและพยากรณ์ ไม่ใช่อุณหภูมิพื้นผิวจากดาวเทียม",
    current,
    current_units: raw.current_units && typeof raw.current_units === "object" ? raw.current_units as Record<string, string> : {},
    hourly,
    hourly_units: raw.hourly_units && typeof raw.hourly_units === "object" ? raw.hourly_units as Record<string, string> : {},
  };
}

export async function loadDashboardData() {
  const [datasets, layers, features, weather] = await Promise.allSettled([
    request<unknown>("/api/datasets/status"),
    request<unknown>("/api/map/layers"),
    request<FeatureCollection>("/api/map/features?limit=250&bbox=100.30,13.45,100.95,14.05"),
    request<unknown>("/api/weather/context"),
  ]);
  const apiReachable = datasets.status === "fulfilled" || layers.status === "fulfilled" || features.status === "fulfilled";
  const normalizedDatasets = datasets.status === "fulfilled"
    ? asItems<Record<string, unknown>>(datasets.value).map(normalizeDataset)
    : demoDatasets;
  const normalizedFeatures = features.status === "fulfilled" && features.value?.type === "FeatureCollection"
    ? normalizeFeatureCollection(features.value, "priority")
    : demoFeatures;
  return {
    datasets: normalizedDatasets,
    layers: layers.status === "fulfilled" ? normalizeLayers(layers.value, normalizedDatasets) : defaultLayers,
    features: normalizedFeatures,
    weather: weather.status === "fulfilled" ? normalizeWeather(weather.value) : null,
    isDemo: !apiReachable || normalizedFeatures === demoFeatures,
  };
}

export async function loadLayerFeatures(layer: string): Promise<FeatureCollection> {
  const value = await request<FeatureCollection>(`/api/map/features?layer=${encodeURIComponent(layer)}&limit=250&bbox=100.30,13.45,100.95,14.05`);
  return normalizeFeatureCollection(value, layer);
}

export async function loadAreaDetail(areaId: string): Promise<AreaProperties> {
  const raw = await request<Record<string, unknown>>(`/api/areas/${encodeURIComponent(areaId)}`);
  const indicators = (raw.indicators || {}) as Record<string, { value?: number; observed_at?: string }>;
  const priority = raw.priority as { score?: number; components?: Record<string, number>; missing_inputs?: string[]; calculated_at?: string } | null;
  const value = (...keys: string[]) => keys.map((key) => indicators[key]?.value).find((item) => typeof item === "number");
  const observed = Object.values(indicators).map((item) => item.observed_at).filter(Boolean) as string[];
  const explanations = priority?.components
    ? Object.entries(priority.components).filter(([, item]) => item != null).map(([key, item]) => `${key}: ${Number(item).toLocaleString("th-TH", { maximumFractionDigits: 2 })}`)
    : [];
  return {
    id: String(raw.id || areaId),
    name_th: String(raw.name_th || "ไม่ทราบชื่อพื้นที่"),
    name_en: typeof raw.name_en === "string" ? raw.name_en : undefined,
    priority_score: priority?.score,
    surface_temperature_c: value("lst_c_mean", "surface_temperature_c", "temperature"),
    temperature_anomaly_c: value("temperature_anomaly", "lst_anomaly_c"),
    population: value("population_estimate", "population", "population_density"),
    population_reference_year: value("population_reference_year"),
    tree_coverage_pct: value("tree_cover_fraction", "tree_coverage_pct"),
    ndvi: value("ndvi"),
    built_up_pct: value("built_fraction", "built_up_pct"),
    quality_status: (raw.boundary_provenance as { quality_status?: string } | undefined)?.quality_status,
    observation_date: (observed.sort().at(-1) || priority?.calculated_at || null),
    priority_explanation: explanations.length ? explanations : priority?.missing_inputs?.map((item) => `ยังขาดข้อมูล: ${item}`),
  };
}

export async function loadAreaHistory(areaId: string): Promise<HistoryPoint[]> {
  const value = await request<unknown>(`/api/areas/${encodeURIComponent(areaId)}/history`);
  const rows = asItems<Record<string, unknown>>(value);
  const grouped = new Map<string, HistoryPoint>();
  for (const row of rows) {
    const date = String(row.date || row.observed_at || "");
    if (!date) continue;
    const point = grouped.get(date) || { date };
    const indicator = String(row.indicator || "");
    const numeric = typeof row.value === "number" ? row.value : null;
    if (indicator.includes("lst") || indicator.includes("temperature")) point.temperature_c = numeric;
    else if (indicator.includes("priority")) point.priority_score = numeric;
    else if (indicator.includes("ndvi")) point.ndvi = numeric;
    grouped.set(date, point);
  }
  return Array.from(grouped.values()).sort((a, b) => a.date.localeCompare(b.date));
}

export async function runOptimization(input: {
  budget_thb: number;
  strategy: string;
  intervention_types: string[];
}): Promise<OptimizationResult> {
  const objectiveMap: Record<string, string> = { equity: "priority", cost_effective: "cost_effectiveness" };
  const raw = await request<Record<string, unknown>>("/api/optimization/run", {
    method: "POST",
    body: JSON.stringify({
      budget_thb: input.budget_thb,
      objective: objectiveMap[input.strategy] || input.strategy,
      enabled_types: input.intervention_types,
    }),
  });
  return normalizeOptimizationResult(raw, input.strategy);
}

function normalizeOptimizationResult(raw: Record<string, unknown>, strategyFallback?: string): OptimizationResult {
  const selected = Array.isArray(raw.selected) ? raw.selected as Array<Record<string, unknown>> : [];
  return {
    run_id: String(raw.run_id),
    strategy: String(raw.objective || strategyFallback || "balanced"),
    budget_thb: Number(raw.budget_thb || 0),
    total_cost_thb: Number(raw.allocated_thb || 0),
    estimated_beneficiaries: Number(raw.estimated_beneficiaries || 0),
    objective_score: Number(raw.objective_value || 0),
    dataset_versions: (raw.input_versions || {}) as Record<string, unknown>,
    is_outdated: Boolean(raw.is_outdated),
    allocations: selected.map(normalizeOptimizationProject),
  };
}

function normalizeOptimizationProject(item: Record<string, unknown>) {
  const geometry = item.geometry as { type?: string; coordinates?: number[] } | undefined;
  return {
    id: String(item.id),
    name: String(item.name_th || item.id),
    intervention_type: String(item.intervention_type || "unknown"),
    cost_thb: Number(item.estimated_cost_thb || 0),
    estimated_beneficiaries: Number(item.estimated_beneficiaries || 0),
    priority_score: Number(item.priority_score || 0),
    longitude: geometry?.type === "Point" ? geometry.coordinates?.[0] : undefined,
    latitude: geometry?.type === "Point" ? geometry.coordinates?.[1] : undefined,
    evidence: typeof item.evidence === "string" || (item.evidence && typeof item.evidence === "object")
      ? item.evidence as string | Record<string, unknown> : undefined,
    uncertainty: typeof item.uncertainty === "string" || (item.uncertainty && typeof item.uncertainty === "object")
      ? item.uncertainty as string | Record<string, unknown> : undefined,
  };
}

export async function loadOptimizationRun(runId: string): Promise<OptimizationResult> {
  const raw = await request<Record<string, unknown>>(`/api/optimization/runs/${encodeURIComponent(runId)}`);
  return normalizeOptimizationResult(raw);
}

export async function downloadOptimizationReport(runId: string): Promise<void> {
  const response = await fetch(endpoint(`/api/optimization/runs/${encodeURIComponent(runId)}/report`), { cache: "no-store" });
  if (!response.ok) {
    const message = await response.text().catch(() => "");
    throw new Error(message || `API ${response.status}`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  const disposition = response.headers.get("content-disposition") || "";
  const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] || `heat-shift-decision-report-${runId}.html`;
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export async function compareScenarios(input: {
  budget_thb: number;
  intervention_types: string[];
}): Promise<OptimizationResult[]> {
  const value = await request<Record<string, unknown>>("/api/scenarios/compare", {
    method: "POST",
    body: JSON.stringify({ budget_thb: input.budget_thb, enabled_types: input.intervention_types }),
  });
  const scenarios = Array.isArray(value.scenarios) ? value.scenarios as Array<Record<string, unknown>> : [];
  return scenarios.map((item) => ({
    run_id: `scenario:${String(item.strategy)}`,
    strategy: String(item.label_th || item.strategy),
    budget_thb: input.budget_thb,
    total_cost_thb: Number(item.allocated_thb || 0),
    estimated_beneficiaries: Number(item.estimated_beneficiaries || 0),
    objective_score: Number(item.objective_value || 0),
    dataset_versions: (value.input_versions || {}) as Record<string, unknown>,
    allocations: Array.isArray(item.allocations)
      ? (item.allocations as Array<Record<string, unknown>>).map(normalizeOptimizationProject)
      : [],
  }));
}

export async function runForecastScenario(horizonYears: 1 | 3 | 5, areaIds?: string[]): Promise<ForecastResult> {
  return request<ForecastResult>("/api/forecast/run", {
    method: "POST",
    body: JSON.stringify({ horizon_years: horizonYears, area_ids: areaIds?.length ? areaIds : null }),
  });
}

export async function loadPriorityRankings(pageSize = 50): Promise<PriorityRankingResult> {
  return request<PriorityRankingResult>(`/api/priority/rankings?page=1&page_size=${pageSize}`);
}

export async function triggerRefresh(source: string, operatorToken: string) {
  const response = await fetch("/api/admin/ingestion", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Local-Admin-Token": operatorToken },
    body: JSON.stringify({ source }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: `API ${response.status}` })) as { detail?: string };
    throw new Error(payload.detail || `API ${response.status}`);
  }
  return response.json() as Promise<{ job_id: string; status: string }>;
}

export async function loadIngestionStatus(jobId: string) {
  return request<{
    job: { id: string; status: string; stage: string; progress: number; error_message?: string | null } | null;
  }>(`/api/ingestion/status?job_id=${encodeURIComponent(jobId)}`);
}

export async function loadObservatoryStatus(): Promise<ObservatorySnapshot> {
  return request<ObservatorySnapshot>("/api/ingestion/status");
}

export async function askAssistant(question: string, areaId?: string) {
  const value = await request<{
    answer_th: string;
    grounding?: Array<Record<string, unknown>>;
    optimization?: Record<string, unknown> | null;
    mode: string;
  }>(
    "/api/assistant/query",
    {
      method: "POST",
      body: JSON.stringify({ question, area_id: areaId || null }),
    },
  );
  return {
    answer: value.answer_th,
    grounding: value.grounding || [],
    optimization: value.optimization ? normalizeOptimizationResult(value.optimization, "balanced") : null,
    mode: value.mode,
  };
}

export function streamUrl() {
  return endpoint("/api/events/stream");
}
