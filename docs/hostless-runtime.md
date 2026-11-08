# HEAT runtime บน Hostless ภายใน shared pool

แพ็กสำหรับบัญชีที่มี Redis 0.2 vCPU / 256 MiB และ PostgreSQL ภายนอกบน Aiven ใช้ `infra/docker/hostless.Dockerfile` กับ Hostless file `infra/hostless/hostless.yaml` ให้แอปหนึ่งตัวมี CPU 0.3 / RAM 768 และหนึ่ง replica รวมกับ Redis เป็น CPU 0.5 / RAM 1024 ตาม entitlement ที่เคยตรวจ ต้องให้หน้า Review ยืนยันยอดรวม $0 และไม่เพิ่ม volume/card/credit ก่อนสร้างจริง ไม่มีการรับรองค่าใช้จ่ายจากไฟล์นี้เพียงอย่างเดียว

## โปรเซสและ persistence

`scripts/hostless_runtime.py` เริ่ม migrations ก่อน แล้วดูแล Next.js, FastAPI, ingestion worker, control dispatcher และ Beat รวม 5 โปรเซสใน container เดียว พอร์ตภายนอกเป็น Next.js ตาม `PORT`; API ฟังเฉพาะ loopback 8000 ใช้ generic proxy ที่ build ด้วย `API_INTERNAL_URL=http://127.0.0.1:8000` งาน admin ยังคงผ่าน App Router ที่ตรวจสิทธิ์ก่อน

เมื่อโปรเซสใดหยุดแม้ exit code 0 ตัวดูแลหยุดกลุ่มที่เหลือและคืน nonzero เพื่อให้ platform restart ทั้งแอป ส่ง SIGTERM และรอ bounded grace ก่อน SIGKILL การทดสอบ child restart ไม่แทนการพิสูจน์ข้อมูลคิวครบหลัง abrupt crash

ข้อมูล/ประวัติ/สถานะงานอยู่ใน PostGIS และ Redis ภายนอก ไม่เปิด PostgreSQL/Redis ใน app filesystem ไม่ต้องมี paid volume Beat schedule, cache และ CA file อยู่ใน scratch `/tmp/heatshift` ซึ่งสร้างใหม่ได้หลัง restart; cursor และ distributed locks อยู่ใน backends ตามเดิม

## Runtime secrets

ตั้งผ่าน private runtime environment ของ Hostless ไม่ใส่ค่าจริงใน Git/build args/รายงาน:

- `DATABASE_URL`: logical database สำหรับ HEAT บน Aiven ใช้ role ที่จำกัดสิทธิ์ให้เหมาะสม
- `DATABASE_CA_PEM_BASE64`: base64 ของ project CA bundle ที่ดาวน์โหลดจาก service จริง ตัวเริ่มระบบ parse CA ก่อนใช้ แล้วบังคับ `sslmode=verify-full` พร้อม hostname verification ไม่เชื่อค่า `sslmode=require/disable` ใน URI
- `REDIS_URL`: `rediss://` ของ Hostless สำหรับ broker/results; ไม่รับ query overrides
- `AUTH_REDIS_URL`: `rediss://` สำหรับ session DB 2
- `AUTH_SESSION_SECRET`: secret สุ่มส่วนตัวอย่างน้อย 32 ตัวอักษร
- `APP_ORIGIN` และ `CORS_ORIGINS`: HTTPS URL จริงหลัง platform จัดสรร domain
- OIDC issuer/client/audience/JWKS/roles และ Earth Engine credentials เมื่อมีสิทธิ์จริง ห้ามสร้าง placeholder แล้วอ้างว่าใช้งานได้

ตัวเริ่มระบบบังคับ production/OIDC, ปิด demo/fixture ingestion, ล้าง legacy admin keys และจำกัด pool 2 / overflow 0 / timeout 10 ต่อ process หาก OIDC ยังไม่ครบ admin operations ยังคงปฏิเสธ และ production/data readiness ยัง blocked ตามจริง ไม่แก้ readiness ให้ผ่านเพื่อเปิด container

Health check ของ platform ใช้ `/api/health` ซึ่งตรวจ DB ผ่าน Next proxy ส่วน `/api/health/ready` และ release audit ยังคงเป็นเกณฑ์ข้อมูล/ระบบที่เข้มกว่า ห้ามใช้ platform Running แทนหลักฐานว่าข้อมูลจริงครบแล้ว

`GET /api/system/runtime` รายงาน cgroup CPU/RAM limits, memory ปัจจุบัน, OOM counters และสถานะโปรเซส โดยไม่เผย PID, env หรือ secrets เป็นการสังเกต ณ เวลาตรวจ ไม่ใช่ peak load หรือการรับรองบิลทั้งบัญชี

## ตรวจโดยไม่รันเซิร์ฟเวอร์ในเครื่องผู้ใช้

Unit tests ตรวจ TLS/CA, configuration ที่ไม่ปลอดภัย, process exit/shutdown และการไม่รายงานตัวเลขศูนย์แทนข้อมูลที่อ่านไม่ได้ การทดสอบ process ใช้ Python subprocess สั้น ๆ ไม่เปิด web/API/worker ในเครื่อง

`scripts/verify_hostless_runtime_ci.py` ปฏิเสธการรันนอก GitHub CI สร้าง image ใน CI ใช้ backends ชั่วคราวที่ปิด provider ทุกแหล่งแล้ว บังคับ CPU 0.3 / RAM 768 MiB / no swap / read-only root ตรวจหน้าเว็บ API admin gates SSE cgroup และ kill/restart โปรเซสจริง ตรวจ source checks เป็นศูนย์ก่อนจบ รายงาน artifact `heatshift-hostless-runtime-ci.json`

การตรวจนี้ใช้ HTTP เฉพาะ loopback ใน CI และ private Compose backends ที่จำกัดด้วย CI guards ไม่ใช้แทน TLS/ใบรับรอง/latency ของ Aiven–Hostless จริง หลังเปิดจริงต้องตรวจ certificate, source schedules/publication, bounded workload และ usage/quote จาก provider อีกครั้ง รวมถึงคิวและ backup/recovery ก่อน production release
