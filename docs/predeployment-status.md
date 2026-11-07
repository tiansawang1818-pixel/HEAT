# สถานะก่อนเปิด HEAT//SHIFT

วันที่ตรวจ: 7 พฤศจิกายน 2026 (Asia/Bangkok)

**ยังไม่ได้ deploy และยังไม่มีเครื่องปลายทางที่ผ่านเงื่อนไขทั้งหมดของผู้ใช้** ไม่มีการเปิดบริการเสียเงิน เพิ่มบัตร สมัคร trial หรือใช้เครื่องผู้ใช้เป็น app server/worker/scheduler งานรอบนี้แก้ source และส่งการทดสอบไป GitHub ภายในเพดานบัญชีที่มีอยู่

ความคืบหน้าการหา hosting: บัญชี Hostless ได้สิทธิ์รวม 0.5 vCPU / 1024 MiB RAM / 512 MiB storage และ provision PostgreSQL/Redis ทดสอบแล้ว Redis ผ่าน TLS, logical DB 0/1/2 และคิวทดสอบหลัง restart จริง พร้อมทดสอบ Celery broker/backend และ node-redis เชื่อม endpoint จริงได้หลังแก้ TLS settings แต่ PostgreSQL ไม่ผ่าน health check แม้ลองเพิ่ม CPU/RAM ภายใน pool จึงยังตรวจ PostGIS ไม่ได้ ยังไม่มี whole-stack capacity test, production app/worker, บัตร/เครดิต หรือ repo integration ดู [ผลทดสอบจริง](no-card-hosting-research.md#ผลทดสอบ-backend-จริง) และ [สคริปต์ตรวจซ้ำ](managed-hosting-checks.md)

## งานที่ทำและ push แล้ว

- Historical ingestion หลายปี: keyset pages แยก Landsat 8/9, frozen date range, จำกัดจำนวนคำขอ/ความพยายามที่รวม retry, PostgreSQL cursor/pending, distributed locks, delivery generation, worker recovery และ cancel/resume เป็น job ใหม่ พร้อมหน้าควบคุมภาษาไทย Flag เรียก Earth Engine จริงยังปิดเป็นค่าเริ่มต้น
- Decision integrity: ปิด intervention/candidate ทั้งหมดได้จริง กันงบเศษบาทโดยปัดขึ้น ไม่มีน้ำหนัก default แฝงใน custom objective และตรวจ OR-Tools กับพอร์ตขนาดเล็กที่ไล่ครบทุกชุดได้
- เก็บรายละเอียด candidate/geometry/cost/evidence กับผล optimization ใหม่ ผลเดิมไม่อ่านทับด้วยรายละเอียดปัจจุบัน และผลเก่าที่ไม่เคยบันทึก snapshot แสดงว่าข้อมูลไม่ครบ
- Scenario A/B ใช้การจัดอันดับตามอุณหภูมิ/ประมาณการผู้รับประโยชน์จริงกับข้อจำกัดเดียวกัน Scenario C ใช้ OR-Tools เปรียบเทียบ objective ด้วยน้ำหนักเดียวกัน หากหลักฐานจัดอันดับไม่ครบแสดง `insufficient_data` ไม่มีแผนที่ผลลดความร้อนสมมติ
- Saved scenario input fingerprint ตรวจต้นทุน/หลักฐาน/ข้อมูลที่เปลี่ยน โดยเก็บ comparison เดิม ผลรวมผู้รับประโยชน์ยังไม่หักซ้ำ และไม่ใช้ศูนย์แทนหลักฐานที่ขาด
- Client updates ตรวจ affected layers, metadata polling, stream outage, event deduplication และ cleanup โดยไม่ย้าย state ของมุมมอง/พื้นที่ที่เลือกไปอยู่ในตัวรับ event
- แผนที่พื้นฐาน OSM จริงแบบ viewport มี style ในเว็บและ attribution ที่ไม่ต้องกดเปิด ไม่ต้องสมัครบัญชี basemap ใหม่ บริการ tile ชุมชนมีข้อจำกัดและไม่มี SLA ไม่ใช้ bulk/prefetch
- Native ARM PostGIS จาก official PostgreSQL/PGDG packages พร้อม Compose override และ CI บน ARM จริง ไม่อาศัย emulation และไม่อ้างว่ามี cloud VM แล้ว
- แก้ Next.js proxy order ให้ dynamic admin routes ผ่านการตรวจสิทธิ์ก่อน generic backend rewrite และตรวจผ่าน HTTPS ทั้งการอนุมัติโครงการ/การควบคุมงานย้อนหลัง
- คู่มือ deployment, วิธีการทางวิทยาศาสตร์, แหล่งต้นทุนสาธารณะสำหรับคนตรวจ และบทนำเสนอ 3 นาทีที่แสดงข้อมูลขาดตามจริง
- Managed hosting checks: สคริปต์ตรวจ PostGIS/TLS/Redis แบบอ่านอย่างเดียวเป็นค่าเริ่มต้น และโหมดตรวจคิวทดสอบหลัง restart ที่ไม่แตะคิวจริง พร้อมแก้ Celery broker/backend SSL และ node-redis SNI ตรวจ client connectivity กับ Redis บน Hostless จริงผ่าน เก็บรายงานที่ไม่มี credentials และ [ข้อความปัญหาที่เตรียมไว้แต่ยังไม่ได้ส่ง](hostless-readiness-support-draft.md)

โค้ดที่ตรวจล่าสุด: `a807f7d666e674ca4db7c57e6e61f8b226b40708` รวม historical/decision-integrity และ managed-hosting/TLS checks เอกสารหรือรายงานที่ commit ต่อจากนี้ไม่เปลี่ยน functional code ที่ CI ตรวจ

## ผลตรวจ

[GitHub Actions run 42374635238](https://github.com/tiansawang1818-pixel/HEAT/actions/runs/42374635238) ผ่านครบทั้ง 5 งาน ตรวจทั้ง job conclusion และ test summaries จาก logs แล้ว:

| งาน | หลักฐาน |
|---|---|
| API | 184 unit/API tests ผ่าน และ dependency audit ผ่าน |
| Web | 38 tests ผ่าน รวม Redis session integration, TypeScript ผ่าน, Next.js production build ผ่าน |
| Stack amd64 | 4 integration tests ผ่าน: PostGIS/Redis/Celery/Beat/SSE และ historical cursor ข้าม session; ซ้อม backup/restore เทียบแถว/ checksum ครบ 22 application tables |
| Native ARM | สร้าง PostGIS/API/Caddy แบบ arm64 และผ่าน integration 4 tests เดียวกันบน ARM จริง |
| Release containers | หน้าเว็บ, admin gates, HTTPS/certificate verification, secure cookies, SSE ผ่าน Caddy/Next, config isolation, bounded latency, encrypted backup download recovery และการส่ง test alert ภายใน CI ผ่าน |

Release audit ใน CI **ตั้งใจยังติด data-readiness blocker** เพราะไม่มีข้อมูลและ credentials ของ provider จริง การผ่านชุดตรวจระบบไม่ใช่หลักฐานว่าข้อมูล/ราคาโครงการ/forecast พร้อมตัดสินใจแล้ว ชุดทดสอบใช้ synthetic fixtures ในฐานข้อมูลแยกและไม่เรียกบัญชีดาวเทียมจริง

## สิ่งที่ยังต้องมีจากภายนอกก่อนเปิดจริง

1. **ทรัพยากรปลายทาง:** เครื่อง/สิทธิ์ใช้ฟรีที่รองรับ PostGIS, Redis และงานต่อเนื่องจริง โดยไม่ใช้บัตรหรือคิดเงินเกินโควตา ยังไม่มีที่ยืนยันครบเงื่อนไข ไม่รับรอง 24/7 จากบริการที่พักตัวหรือ scheduled CI
2. **สิทธิ์ข้อมูลและตัวตน:** Google/Earth Engine project ที่ผ่านการยืนยันการใช้งาน/โควตาและสิทธิ์ credentials พร้อม SSO identity/role ของผู้ดูแลจริง ห้ามใส่ secret ในแชตหรือ Git
3. **ข้อมูลและหลักฐานการลงทุน:** ภาพดาวเทียมที่ผ่าน calibration/coverage validation และประวัติที่เพียงพอ; OSM/provider checks ที่สำเร็จ; ราคาต่อจุดที่มีหลักฐานและผู้รับผิดชอบยืนยัน site feasibility, land control และ design/accessibility ไม่มีการอนุมัติแทนเจ้าของงาน
4. **ตรวจบนเครื่องและข้อมูลจริง:** DNS/TLS, SSO, automatic schedules, provider quotas, raster/vector alignment, browser UX, load/capacity, alert delivery และ off-host backup/recovery จากปลายทางจริง จากนั้นรัน release audit อีกครั้ง

ข้อมูล WorldPop/ขอบเขตที่เคยตรวจแบบ read-only ไม่ทดแทนข้อมูล LST/NDVI ที่ยังขาด และไม่ทำให้การพยากรณ์ห้าปีผ่านเกณฑ์โดยอัตโนมัติ ไม่ฝึกโมเดลหรือแสดง prediction เมื่อ historical/temporal/spatial validation ยังไม่ผ่าน งบ optimizer เป็นเงินลงทุนเริ่มต้น; การบำรุงรักษาและ lifecycle assumptions ต้องมีหลักฐานแยก

ในช่วงที่ยังไม่มีทรัพยากรข้างต้น ไม่มีงาน background ของ production รันอยู่ การเปิดแชตหรือปิดฝาเครื่องไม่ได้ทำให้ระบบกลายเป็นบริการ cloud ที่ทำงานต่อเนื่อง

เอกสารที่เกี่ยวข้อง: [hosting constraints](hosting-constraints.md), [historical ingestion](historical-ingestion.md), [server deployment](server-deployment.md), [container architectures](container-architectures.md), [science/limitations](scientific-methodology.md), [cost evidence](cost-evidence-sources.md), [demo script](demo-script-th.md)
