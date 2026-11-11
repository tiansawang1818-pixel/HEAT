import { adminPost, request } from "@/lib/api";

export interface BackfillBudget {
  max_assets: number;
  max_catalogue_requests: number;
  step_delay_seconds: number;
}

export interface BackfillDefinition extends BackfillBudget {
  source: "landsat" | "sentinel" | "dynamic-world";
  start_date: string;
  end_date: string;
  page_size: number;
  window_days: number;
  /** Calendar months to scan, e.g. [1, 2, 3, 4] for the dry season; all when omitted. */
  months?: number[];
}

export interface BackfillJob {
  id: string;
  status: string;
  stage: string;
  configuration: BackfillDefinition;
  cursor: { window_start?: string };
  catalogue_requests: number;
  processing_attempts: number;
  pending_assets: number;
  skipped: number;
  scanned: number;
  next_step_at: string | null;
  resumed_from_job_id: string | null;
  error_message: string | null;
  can_resume: boolean;
}

export interface BackfillStatus {
  enabled: boolean;
  reason: string;
  budget_note: string;
  items: BackfillJob[];
}

export function loadBackfills(signal?: AbortSignal) {
  return request<BackfillStatus>("/api/ingestion/backfills", { signal });
}

export function startBackfill(definition: BackfillDefinition, credential: string) {
  return adminPost<{ job_id: string; status: string }>("/api/admin/backfills", credential, { ...definition });
}

export function controlBackfill(id: string, action: "resume" | "cancel", budget: BackfillBudget, credential: string) {
  return adminPost<{ job_id?: string; status: string }>(
    `/api/admin/backfills/${encodeURIComponent(id)}/${action}`, credential, action === "resume" ? { ...budget } : {},
  );
}
