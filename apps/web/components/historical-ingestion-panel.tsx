"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { controlBackfill, loadBackfills, startBackfill, type BackfillDefinition, type BackfillStatus } from "@/lib/backfills";
import { formatThaiDate } from "@/lib/utils";
import { Button } from "@/components/ui/button";

const stageLabels: Record<string, string> = {
  queued: "รอส่งเข้าคิว", dispatched: "ส่งเข้าคิวแล้ว", historical_step: "กำลังทำงาน",
  catalogue_query: "ตรวจรายการภาพ", catalogue_staged: "บันทึกรายการแล้ว", processing: "ประมวลผลภาพ",
  waiting: "รอรอบถัดไป", source_busy: "รอแหล่งข้อมูลว่าง", retry_scheduled: "รอลองใหม่",
  worker_recovery: "กู้คืนงานค้าง", dispatch_retry: "รอคิวพร้อม", budget_exhausted: "ถึงเพดานที่กำหนด",
  completed: "ค้นช่วงเวลานี้ครบแล้ว", failed: "ล้มเหลว", cancelled: "ยกเลิกแล้ว", cancel_requested: "รอจบภาพปัจจุบันแล้วหยุด",
};

export function HistoricalIngestionPanel({ credential }: { credential: string }) {
  const [status, setStatus] = useState<BackfillStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [definition, setDefinition] = useState<BackfillDefinition>({
    source: "landsat", start_date: "2020-01-01", end_date: "2025-01-01",
    max_assets: 12, max_catalogue_requests: 50, step_delay_seconds: 600, page_size: 10, window_days: 16,
  });
  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const result = await loadBackfills(signal);
      if (!signal?.aborted) { setStatus(result); setError(null); }
    } catch {
      if (!signal?.aborted) setError("อ่านสถานะงานย้อนหลังไม่สำเร็จ ข้อมูลที่เห็นอาจยังไม่ล่าสุด");
    }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let inFlight = false;
    const tick = async () => {
      if (inFlight) return;
      inFlight = true;
      try { await refresh(controller.signal); } finally { inFlight = false; }
    };
    void tick();
    const timer = setInterval(() => void tick(), 15_000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [refresh]);

  async function mutate(action: () => Promise<unknown>, success: string) {
    setBusy(true); setError(null); setMessage(null);
    try { await action(); setMessage(success); await refresh(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "ทำรายการไม่สำเร็จ"); }
    finally { setBusy(false); }
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    void mutate(() => startBackfill(definition, credential), "บันทึกงานพร้อมจุดทำต่อแล้ว ระบบจะส่งเข้าคิวตามรอบตรวจ");
  }
  const allowed = !!status?.enabled && !!credential && !busy;
  return <section className="readiness-panel historical-ingestion" aria-label="นำเข้าข้อมูลย้อนหลัง">
    <div className="readiness-heading"><div><span>คลังข้อมูลระยะยาว</span><strong>นำเข้าข้อมูลย้อนหลังเป็นชุด</strong>
      <small>เก็บประวัติเดิมและวันที่สังเกตการณ์จริง พร้อมจำกัดงานและทำต่อจากจุดค้าง</small></div></div>
    {status && !status.enabled && <p className="readiness-error" role="status">{status.reason}</p>}
    <p className="readiness-scope">{status?.budget_note || "จำนวนครั้งประมวลผลรวมการลองใหม่ และไม่ได้รับรองโควตาหรือค่าใช้จ่ายผู้ให้บริการ"}</p>
    <details className="readiness-details"><summary>กำหนดช่วงเวลาและเพดานงาน</summary>
      <form onSubmit={submit} className="historical-form">
        <label><span>แหล่งดาวเทียม</span><select value={definition.source} onChange={e => setDefinition(d => ({ ...d, source: e.target.value as BackfillDefinition["source"] }))}>
          <option value="landsat">Landsat 8/9 — อุณหภูมิพื้นผิว</option><option value="sentinel">Sentinel-2 — NDVI</option><option value="dynamic-world">Dynamic World — สิ่งปกคลุมดิน</option>
        </select></label>
        <label><span>ตั้งแต่วันที่ (ค.ศ.)</span><input required type="date" value={definition.start_date} onChange={e => setDefinition(d => ({ ...d, start_date: e.target.value }))} /></label>
        <label><span>ก่อนวันที่ (ไม่รวมวันที่เลือก)</span><input required type="date" value={definition.end_date} onChange={e => setDefinition(d => ({ ...d, end_date: e.target.value }))} /></label>
        <label><span>ประมวลผลสูงสุด (ครั้ง รวมลองใหม่)</span><input type="number" required min={1} max={100} value={definition.max_assets} onChange={e => setDefinition(d => ({ ...d, max_assets: Number(e.target.value) }))} /></label>
        <label><span>ตรวจรายการสูงสุด (ครั้ง)</span><input type="number" required min={1} max={500} value={definition.max_catalogue_requests} onChange={e => setDefinition(d => ({ ...d, max_catalogue_requests: Number(e.target.value) }))} /></label>
        <label><span>เว้นแต่ละชุด (วินาที)</span><input type="number" required min={60} max={86400} value={definition.step_delay_seconds} onChange={e => setDefinition(d => ({ ...d, step_delay_seconds: Number(e.target.value) }))} /></label>
        <p>เริ่มจากเพดานต่ำและตรวจโควตาในบัญชีผู้ให้บริการก่อนเพิ่มงาน ภาพที่เมฆมากอาจไม่ผ่านเกณฑ์ และประวัติห้าปีไม่ได้รับรองว่าแบบจำลองอนาคตจะผ่านการทดสอบ</p>
        <Button type="submit" disabled={!allowed}>สร้างงานย้อนหลัง</Button>
      </form>
    </details>
    {error && <p className="readiness-error" role="alert">{error}</p>}
    {message && <p className="readiness-privacy" role="status">{message}</p>}
    <div className="historical-jobs">{status?.items.map(job => <article key={job.id}>
      <div><strong>{job.configuration.source} · {job.configuration.start_date} → {job.configuration.end_date}</strong><span>{stageLabels[job.stage] || "กำลังตรวจสถานะ"}</span></div>
      <p>ประมวลผล {job.processing_attempts}/{job.configuration.max_assets} ครั้ง · ตรวจรายการ {job.catalogue_requests}/{job.configuration.max_catalogue_requests} ครั้ง · รอ {job.pending_assets} ภาพ · ข้ามข้อมูลเดิม {job.skipped} ภาพ</p>
      {job.cursor.window_start && <p>ตรวจต่อจาก {job.cursor.window_start} · {job.next_step_at && `รอบถัดไป ${formatThaiDate(job.next_step_at, true)}`}</p>}
      {job.error_message && <p className="readiness-error">{job.error_message}</p>}
      {job.resumed_from_job_id && <small>ทำต่อจากงาน {job.resumed_from_job_id}</small>}
      <small>รหัสงาน {job.id}</small>
      <div className="historical-actions">
        {job.can_resume && <Button size="sm" variant="outline" disabled={!allowed} onClick={() => void mutate(() => controlBackfill(job.id, "resume", definition, credential), "สร้างงานใหม่จากจุดค้างแล้ว เก็บผลและเพดานเดิมไว้อ้างอิง")}>ทำต่อด้วยเพดานข้างต้น</Button>}
        {["queued", "running"].includes(job.status) && <Button size="sm" variant="outline" disabled={!credential || busy} onClick={() => void mutate(() => controlBackfill(job.id, "cancel", definition, credential), "รับคำขอหยุดแล้ว ภาพที่เริ่มประมวลผลจะจบอย่างปลอดภัยก่อนหยุด")}>หยุดหลังภาพปัจจุบัน</Button>}
      </div>
    </article>)}</div>
    {status && !status.items.length && <p className="readiness-scope">ยังไม่มีงานย้อนหลัง</p>}
  </section>;
}
