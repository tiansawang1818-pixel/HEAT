import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";
import type { Geometry } from "geojson";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatThaiDate(value?: string | null, withTime = false) {
  if (!value) return "ยังไม่มีข้อมูล";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "ไม่ทราบเวลา";
  return new Intl.DateTimeFormat("th-TH", {
    dateStyle: "medium",
    ...(withTime ? { timeStyle: "short", timeZone: "Asia/Bangkok" } : {}),
  }).format(date);
}

export function formatNumber(value?: number | null, digits = 0) {
  if (value == null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("th-TH", { maximumFractionDigits: digits }).format(value);
}

export function formatBaht(value: number) {
  if (value >= 1_000_000) return `฿${formatNumber(value / 1_000_000, 1)} ล้าน`;
  return `฿${formatNumber(value)}`;
}

export function freshnessLabel(status: DatasetStatus["status"]) {
  return {
    up_to_date: "เป็นปัจจุบัน",
    updating: "กำลังอัปเดต",
    stale: "ข้อมูลเก่า",
    unavailable: "ไม่พร้อมใช้งาน",
    error: "เกิดข้อผิดพลาด",
  }[status];
}

export type FreshnessState = "up_to_date" | "updating" | "stale" | "unavailable" | "error";

export interface DatasetStatus {
  source_name: string;
  dataset_id: string;
  layer_name?: string;
  status: FreshnessState;
  last_source_observed_at?: string | null;
  last_checked_at?: string | null;
  last_published_at?: string | null;
  next_check_at?: string | null;
  layer_version?: number;
  quality_status?: string;
  status_reason?: string;
}

export interface AreaProperties {
  id: string;
  name_th: string;
  name_en?: string;
  priority_score?: number | null;
  surface_temperature_c?: number | null;
  temperature_anomaly_c?: number | null;
  population?: number | null;
  population_reference_year?: number | null;
  tree_coverage_pct?: number | null;
  ndvi?: number | null;
  built_up_pct?: number | null;
  quality_status?: string;
  observation_date?: string | null;
  priority_explanation?: string[];
}

export interface GeoFeature {
  type: "Feature";
  id?: string | number;
  geometry: Geometry;
  properties: AreaProperties & Record<string, unknown>;
}

export interface FeatureCollection {
  type: "FeatureCollection";
  features: GeoFeature[];
}

export interface MapLayerMeta {
  id: string;
  name_th: string;
  description_th?: string;
  unit?: string;
  status: FreshnessState;
  layer_version?: number;
  observation_date?: string | null;
  source_name?: string;
}

export interface OptimizationProject {
  id: string;
  name: string;
  intervention_type: string;
  cost_thb: number;
  estimated_beneficiaries?: number;
  priority_score?: number;
  latitude?: number;
  longitude?: number;
  evidence?: string | Record<string, unknown>;
  uncertainty?: string | Record<string, unknown>;
}

export interface OptimizationResult {
  run_id: string;
  strategy: string;
  budget_thb: number;
  total_cost_thb: number;
  estimated_beneficiaries: number;
  objective_score?: number;
  dataset_versions?: Record<string, unknown>;
  allocations: OptimizationProject[];
  is_outdated?: boolean;
  is_demo?: boolean;
}

export interface HistoryPoint {
  date: string;
  temperature_c?: number | null;
  priority_score?: number | null;
  ndvi?: number | null;
  lower_bound?: number | null;
  upper_bound?: number | null;
}

export interface ObservatorySnapshot {
  queue: Record<"queued" | "running" | "completed" | "failed", number>;
  summary: {
    discovered_assets_latest_checks: number;
    processed_assets_latest_checks: number;
    failed_jobs_total: number;
    sources_with_errors: number;
  };
  coverage: {
    geographic_areas: number;
    areas_with_indicators: number;
    percent: number;
  };
  source_checks: Array<{
    source_id: string;
    source_name: string;
    dataset_id: string;
    status: string;
    checked_at?: string | null;
    completed_at?: string | null;
    discovered_count: number;
    processed_count: number;
    provider_quota: Record<string, unknown>;
    error_message?: string | null;
  }>;
  recent_jobs: Array<{
    id: string;
    job_type: string;
    status: string;
    stage: string;
    progress: number;
    attempts: number;
    requested_by: string;
    created_at?: string | null;
    error_message?: string | null;
  }>;
  recent_events: Array<{
    id: string;
    type: string;
    layer?: string | null;
    layer_version?: number | null;
    created_at?: string | null;
    payload: Record<string, unknown>;
  }>;
}
