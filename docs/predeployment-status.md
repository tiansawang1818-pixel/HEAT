# สถานะ HEAT//SHIFT ก่อน production release

> **อัปเดต 12 พฤศจิกายน 2026:** เนื้อหาด้านล่างเป็นบันทึกหลักฐานของวันที่ 8 พฤศจิกายน เก็บไว้เป็นประวัติ สถานะบนเว็บจริงล่าสุดคือ
>
> - เว็บนำร่อง <https://heatshift.hostless.app/> ให้บริการต่อเนื่องบน Hostless + Aiven Free; ระหว่าง deploy รุ่นใหม่อาจตอบ 404 สั้น ๆ
> - ขอบเขต 50 เขตเผยแพร่แล้ว (provider BMA / data.go.th, validation ผ่าน) ข้อความ "เผยแพร่ 0 เขต" และขั้นตอนข้อ 2 ด้านล่างจึงเสร็จแล้ว
> - แหล่งข้อมูลทั้ง 7 แหล่ง (BMA, Landsat 8/9, Sentinel-2, Dynamic World, WorldPop, Open-Meteo, OpenStreetMap) เผยแพร่เวอร์ชันที่ผ่าน validation และติดตามอัตโนมัติ
> - `/api/system/readiness` = `ready` (รวม OIDC/RBAC และ Earth Engine identity), `/api/system/data-readiness` มี `data_ready: true`, `forecast_only: true` แต่ `investment_decisions_ready: false` เพราะต้นทุน/หลักฐานหน้างานยังรออนุมัติ
> - API รันด้วย gunicorn worker เดียวที่รีไซเคิลทุก ~400 requests; รายละเอียดอยู่ใน [hostless-runtime.md](hostless-runtime.md)


ตรวจวันที่ 8 พฤศจิกายน 2026 (Asia/Bangkok); timestamps ใน JSON ใช้ UTC

**แก้ Hostless rollout สำเร็จแล้ว** เว็บนำร่องที่ https://heatshift.hostless.app/ ใช้รุ่นใหม่ `f45573d2fe8977f32061635ca3cd7198215ad109` จริง หน้า Deployments แสดง `Deployed LIVE` สำหรับ deployment `6af080a1cb0b276633784aaf` ตรวจ HTTPS และคำขอพร้อมกัน 8 รายการขณะ SSE เปิดแล้วตอบ 200 ครบ แต่ยังไม่ผ่าน production release ด้านข้อมูล/SSO

สาเหตุที่แก้: [Hostless ให้เวลาแต่ละ health probe 1 วินาที](https://docs.hostless.cloud/app/healthcheck) เดิมใช้ `/api/health` ที่รอ Aiven/connection pool เปลี่ยน platform probe เป็น `/api/health/live` ที่ไม่ checkout DB และตรวจว่า provider รับค่าจริงแล้ว คง `/api/health`, `/api/health/ready`, system/data readiness และ admin authentication ไว้ ไม่เพิ่ม CPU/RAM/replica/volume หรือผูกบัตร รอบแก้รุ่นนี้ไม่สั่ง Pause/Restart เว็บด้วยมือ

เพิ่ม startup logs แยก configuration/migrations/processes โดยไม่พิมพ์ URI, secrets หรือ exception message Log จริงของรุ่นใหม่แสดง migrations สำเร็จประมาณ 9 วินาที เริ่มครบ 5 โปรเซส และ API ฟังพอร์ต 18000 ก่อน provider เปลี่ยน traffic

ประวัติก่อนแก้: รุ่น `daad58e`, `96ef703`, `eefe886` เคยไม่ผ่าน readiness และให้บริการด้วย `5f24c4a` ช่วงนั้นผู้ใช้อนุญาต Pause/Resume เฉพาะแอปไม่เกิน 10 นาที ตรวจ API กลับ 200 เวลา 20:46 น. ภายในประมาณ 6 นาที ไม่หยุด Aiven/Redis รายงานเดิมเก็บเป็นหลักฐานประวัติ ไม่ใช้เป็นสถานะปัจจุบัน

## สิ่งที่ยืนยันบน cloud จริง

- Review quote ของแอป $0.00, CPU 0.3 / RAM 768 MiB / replica 1 / ไม่มี volume รวม Redis 0.2 / 256 เท่ากับ entitlement CPU 0.5 / RAM 1024 ไม่มี headroom สำหรับเพิ่ม replica โดยอาศัย free pool เดิม ไม่รับประกันเงื่อนไขราคาในอนาคต
- HTTPS ใช้ public CA; API health และ runtime endpoint ตอบจริง ระบบรายงานครบ 5 โปรเซส CPU limit 0.3 / memory limit 805,306,368 bytes; snapshots ระหว่างการตรวจจริงเวลา 21:10 ใช้ RAM สูงสุดที่สุ่มวัด 526,761,984 bytes และ OOM kill 0 ไม่ใช่ peak ingestion/optimizer
- Aiven Free service `heat-postgis-check`: PostgreSQL 18.6 / PostGIS 3.6.4; logical database แอป `heatshift`, role `heatshift_runtime` จำกัด connection 10, ไม่มี superuser/create database/create role/replication และไม่มีสิทธิ์แก้ migrations ใช้ TLS verify-full และ provider CA
- scheduler เรียก Open-Meteo จริงอัตโนมัติและเผยแพร่เวอร์ชันพร้อม validation report แล้ว บริบทอากาศเป็น model estimate/forecast ไม่ใช่ satellite LST
- BMA national catalog ตอบ 403 และยังมีขอบเขตที่เผยแพร่ 0 เขต ข้อมูลดาวเทียม/ประชากร/priority/โครงการลงทุนยังไม่พร้อม ไม่สร้างข้อมูลมาทดแทน
- ต้นทาง BMA CKAN ที่ data.bangkok.go.th ตรวจแบบอ่านอย่างเดียวได้ HTTP 200 สำหรับ dataset/resource เดียวกัน ต้นทางระบุ format `shapfile` และไฟล์ `.zip` วันที่แก้ไขจริง 2021-03-09 โค้ดรองรับพร้อม validation ของ ZIP/CRS/50 เขต แต่ยังไม่ได้ยืนยัน publication บน Hostless

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
- Capacity collector ใน isolated CI วัด cgroup ของทั้ง 7 services พร้อม GET แบบตรวจ TLS แยก anonymous memory/file cache และไม่รวม peak จากคนละเวลา ปิดแหล่งข้อมูลก่อนเริ่ม scheduler โดยคง readiness gate ของแอปไว้ตามเดิม หากข้อมูลวัดไม่ครบหรือ baseline ต่ำกว่า pool จะไม่เปลี่ยนเป็นอนุมัติ production
- เตรียม PostGIS capability probe สำหรับ database ใหม่ `heat-pg16-check`: บังคับ TLS/ชื่อปลายทาง ปฏิเสธตารางแอปที่มีอยู่ แยก flag เปิด extension ถาวรอย่างชัดเจนจาก transaction ของ geometry/index ที่บังคับ rollback ตรวจ CRS, GeoJSON, GiST plan และ spatial query โดยไม่สร้าง observation หรือ publication ผ่านการรันซ้ำ/ล้มเหลวกลางทางใน isolated PostGIS จริงทั้งสองสถาปัตยกรรมแล้ว [วิธีใช้](managed-hosting-checks.md#postgis-แบบ-rollback-only-ในฐานข้อมูลใหม่)
- เพิ่ม provider CA สำหรับ Aiven, จำกัด connection pool ต่อ process และคืน connection ของ SSE ก่อนส่ง frame/รอ ตรวจด้วย single-connection pool ว่า publisher ยังเขียน publication ได้ขณะ map client เชื่อมอยู่ ใช้ probe กับ fresh `heat-postgis-check` บน Aiven จริงผ่านแล้ว

## ผลตรวจโค้ดล่าสุด

Source `f45573d2fe8977f32061635ca3cd7198215ad109` ผ่าน [GitHub Actions 42487644459](https://github.com/tiansawang1818-pixel/HEAT/actions/runs/42487644459) ครบ 5 jobs:

- API 241 tests และ dependency audit
- Web 38 tests, TypeScript และ production build
- PostGIS/Redis/Celery/Beat/SSE integration 7 tests บน amd64 และ ARM จริง พร้อม isolated backup/restore
- Release containers: HTTPS/admin gates/cookies/SSE, bounded reads, encrypted backup download recovery และ alert delivery
- Combined runtime ภายใต้ CPU 0.3 / RAM 768 MiB / read-only root / no swap ใช้พอร์ตเว็บ 8000 ตาม Hostless ตรวจคำขอเริ่มต้นพร้อมกัน 8 รายการขณะ SSE เปิด และ kill/restart worker จริง ผ่านทั้ง 5 โปรเซส RAM สูงสุดใน 10 samples 483,352,576 bytes เพิ่มการ pause PostgreSQL ใน CI แล้ว probe ตอบภายใน 0.094 วินาทีสูงสุดจาก 3 คำขอ และตรวจ DB อีกครั้งหลัง unpause ดู [รายงาน CI](verification/hostless-readiness-fix-ci-2026-11-08.json)

แก้ pool wait ของ SSE ให้ทำงานนอก event loop และ batch source status จาก 3 reads ต่อแหล่งเป็นรวมไม่เกิน 4 queries ต่อรายการแหล่งข้อมูล ทดสอบการคืน connection ขณะ pool เต็มทั้งตอน connect/poll และคง observation/check timestamps แยกกัน ตรวจ ARM readiness ด้วย TCP เพื่อไม่รับ temporary initdb server เป็นฐานข้อมูลพร้อมใช้งาน

[Release audit ผ่าน HTTPS จริง](verification/hostless-readiness-fixed-release-audit-2026-11-08.json) ยังคง blocked ตามจริง: API ready แต่ configuration ขาด OIDC และ GEE_PROJECT; data/SSO ยังไม่ครบ ไม่ลดเกณฑ์เพื่อเปิด release

## สิ่งที่ยังเหลือ

1. ด่าน Hostless rollout ผ่านแล้ว ดู [ผลตรวจปลายทางจริง](verification/hostless-readiness-fixed-live-2026-11-08.json); ยังต้องตรวจภาระงาน ingestion/optimizer จริงซึ่งต่างจาก bounded reads ที่ผ่านแล้ว
2. ยืนยัน BMA publication 50 เขตจาก scheduler แล้วตรวจ OSM/WorldPop และ geographic alignment; ห้ามใช้วันที่ ingest แทนวันที่ resource
3. สิทธิ์ Earth Engine project/credentials ที่ได้รับอนุมัติ และ OIDC identity/roles ของผู้ดูแลจริง ยังไม่มี ห้ามใส่ placeholder หรือส่ง secret ในแชต/Git
4. ภาพ LST/NDVI/land cover ที่ผ่าน validation, ประวัติหลายฤดู และประชากรพร้อมปีอ้างอิง ก่อนคำนวณ priority/แนวโน้ม/สถานการณ์อนาคต
5. หลักฐานราคาต่อโครงการและการยืนยัน site feasibility/สิทธิ์พื้นที่โดยผู้รับผิดชอบ ก่อนแผนลงทุนใช้งานจริง
6. ทดสอบโหลด ingestion/optimizer จริง, Redis abrupt-crash recovery/ความคงทนคิว, off-host backup/restore และ alert delivery จากปลายทางจริง ชุดตรวจ CI ไม่แทนสิ่งเหล่านี้

ผู้ใช้ยังไม่อนุญาตส่งข้อความหา support ไม่มีการติดต่อแทนผู้ใช้ PostgreSQL เดิมบน Hostless ยังคง recovery window ถึง 15 พฤศจิกายน 2026 เวลา 00:29:23 น. ไม่สร้างบัญชีซ้ำหรือข้าม storage quota

Aiven service เป็น Free จริงแม้มี trial banner ของบัญชี; Free service ไม่ใช้ trial credits แต่มี inactivity policy และไม่มี SLA จึงไม่รับประกัน uptime 24/7 ดู [Aiven readiness](aiven-readiness.md), [combined runtime](hostless-runtime.md), [ข้อจำกัดวิทยาศาสตร์](scientific-methodology.md) และ [deployment](server-deployment.md)

รายงานปัจจุบันรวมใน main แล้ว รายงานก่อนแก้ยังเก็บแยกตาม timestamps เพื่อไม่เปลี่ยนประวัติให้ดูเหมือนผ่านตั้งแต่แรก
