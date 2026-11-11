"use client";

import { useEffect, useState } from "react";
import { loadPublishedDataReadiness } from "@/lib/api";
import { cn, formatThaiDate, type PublishedDataReadiness } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";

export function DataReadinessCard() {
  const [report, setReport] = useState<PublishedDataReadiness | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let inFlight = false;
    const refresh = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const value = await loadPublishedDataReadiness(controller.signal);
        if (!controller.signal.aborted) { setReport(value); setError(null); }
      } catch {
        if (!controller.signal.aborted) setError("ยังตรวจข้อมูลและหลักฐานไม่ได้ กรุณาตรวจการเชื่อมต่อระบบ");
      } finally { inFlight = false; }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 60_000);
    return () => { controller.abort(); clearInterval(timer); };
  }, []);

  return <section className={cn("readiness-panel", report?.data_ready ? "ready" : "blocked")} aria-label="ข้อมูลและหลักฐานก่อนเปิดใช้">
    <div className="readiness-heading">
      <div><span>ข้อมูลและหลักฐานก่อนเปิดใช้</span><strong>{report ? report.data_ready ? (report.forecast_only ? "ข้อมูลพร้อมใช้ · โหมดคาดการณ์" : report.investment_decisions_ready === false ? "ข้อมูลพร้อมใช้ · การลงทุนจริงยังรอหลักฐานรายจุด" : "ผ่านการตรวจระเบียนข้อมูลนำร่อง") : "ยังมีข้อมูลหรือหลักฐานที่ต้องเติม" : "กำลังตรวจข้อมูลจริงและโครงการ"}</strong>
        <small>{report ? `ตรวจ ${formatThaiDate(report.generated_at, true)}` : "อ่านผลจากข้อมูลที่เผยแพร่และรายงานตรวจสอบจริง"}</small></div>
      {report && <Badge>{report.summary.blocked} ต้องจัดการ · {report.summary.warning} ต้องทบทวน</Badge>}
    </div>
    {error && <p className="readiness-error" role="status">{error}{report ? " · แสดงผลตรวจครั้งก่อน" : ""}</p>}
    {report && <>
      <p className="readiness-privacy">ขอบเขตทางการ {report.coverage.official_areas} พื้นที่ · ข้อมูลครบ {report.coverage.complete_pilot_areas} พื้นที่ · โครงการมีหลักฐาน {report.coverage.evidence_backed_candidates} จุด</p>
      <details className="readiness-details">
        <summary>ดูข้อมูลที่ขาดและงานที่ต้องยืนยันก่อนเปิดจริง</summary>
        <div className="readiness-check-grid">{report.checks.map(check => <article key={check.code} className={check.status}>
          <span className="readiness-status-dot" role="img" aria-label={check.status === "ready" ? "ผ่าน" : check.status === "warning" ? "ควรทบทวน" : "ต้องจัดการ"} />
          <span><strong>{check.label_th}</strong><small>{check.detail_th}</small></span>
        </article>)}</div>
        <p className="readiness-scope">ฉากทัศน์อนาคต: {report.future_scenarios_available ? "มีผลผ่านเกณฑ์สำหรับตรวจสอบ" : "ยังไม่ผ่านเกณฑ์ข้อมูลหรือแบบจำลอง"}</p>
        <ul className="release-review-list">{report.external_verification_required_th.map(item => <li key={item}>{item}</li>)}</ul>
        <p className="readiness-scope">{report.limitations_th}</p>
      </details>
    </>}
  </section>;
}
