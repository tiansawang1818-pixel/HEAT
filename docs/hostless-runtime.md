# HEAT runtime บน Hostless ภายใน shared pool

แพ็กสำหรับบัญชีที่มี Redis 0.2 vCPU / 256 MiB และ PostgreSQL ภายนอกบน Aiven ใช้ `infra/docker/hostless.Dockerfile` กับ Hostless file `infra/hostless/hostless.yaml` ให้แอปหนึ่งตัวมี CPU 0.3 / RAM 768 และหนึ่ง replica รวมกับ Redis เป็น CPU 0.5 / RAM 1024 ตาม entitlement ที่เคยตรวจ ต้องให้หน้า Review ยืนยันยอดรวม $0 และไม่เพิ่ม volume/card/credit ก่อนสร้างจริง ไม่มีการรับรองค่าใช้จ่ายจากไฟล์นี้เพียงอย่างเดียว

## โปรเซสและ persistence

`scripts/hostless_runtime.py` เริ่ม migrations ก่อน แล้วดูแล Next.js, FastAPI, ingestion worker และ control dispatcher (ฝัง Beat ไว้ด้วย `-B` เพื่อประหยัดแรมราว 90 MB) รวม 4 โปรเซสใน container เดียว พอร์ตภายนอกเป็น Next.js ตาม `PORT` ที่ provider กำหนด (Hostless ส่ง 8000 ในการเปิดจริงครั้งแรก); API ฟังเฉพาะ loopback 18000 ใช้ generic proxy ที่ build ด้วย `API_INTERNAL_URL=http://127.0.0.1:18000` งาน admin ยังคงผ่าน App Router ที่ตรวจสิทธิ์ก่อน หาก provider กำหนดพอร์ตเว็บชนกับพอร์ต API ส่วนตัว ตัวเริ่มระบบปฏิเสธก่อนเปิดโปรเซส

FastAPI รันผ่าน gunicorn worker เดียว ที่ถูกรีไซเคิลทุก 400±100 requests เพื่อคุมแรม ไม่ใช้ `--preload` เพราะ master จะถือสำเนาแอปเพิ่มราว 130 MB worker ที่ถูกรีไซเคิลจะข้ามรอบ `create_all`/`ensure_sources`/`seed_demo_data` เมื่อมีไฟล์ `API_BOOTSTRAP_MARKER` (`/tmp/heatshift/api-bootstrapped`) แล้ว เพราะรอบนั้นวิ่งไปฐานข้อมูลระยะไกลหลายรอบ ระหว่างนั้นไม่มี worker รับคำขอ ทำให้ proxy ตัดที่ราว 30 วินาที

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

Health check ของ platform ใช้ `/api/health/live` ผ่าน Next proxy ไป API หลัง startup สำเร็จ โดยไม่รอ DB เนื่องจาก [Hostless ให้เวลาแต่ละ probe 1 วินาที](https://docs.hostless.cloud/app/healthcheck) ส่วน `/api/health` ยังตรวจ DB, `/api/health/ready` ยังคืน 503 เมื่อ DB ใช้ไม่ได้ และ `/api/system/readiness`, data-readiness และ release audit ยังคงตรวจ configuration/ข้อมูล/SSO ห้ามใช้ platform Running แทนหลักฐานว่าข้อมูลจริงครบแล้ว

ตัวเริ่มระบบบันทึก phase ของ configuration/migrations/processes พร้อมระยะเวลาของ migrations และชื่อชนิดข้อผิดพลาดเท่านั้น ไม่บันทึก exception message, URI หรือ secrets เพื่อแยกปัญหาก่อน API เริ่มฟังพอร์ตได้

`GET /api/system/runtime` รายงาน cgroup CPU/RAM limits, memory ปัจจุบัน, OOM counters และสถานะโปรเซส โดยไม่เผย PID, env หรือ secrets เป็นการสังเกต ณ เวลาตรวจ ไม่ใช่ peak load หรือการรับรองบิลทั้งบัญชี

SSE ทำ synchronous database reads ผ่าน thread pool และคืน connection ก่อนส่ง frame เพื่อไม่ให้การรอ pool ขัดขวาง event loop/การปิดคำขออื่น Status APIs ใช้ window queries รวมแหล่งข้อมูล แทนการอ่าน 3 ครั้งต่อแหล่งข้อมูล ซึ่งลด round trips ระหว่าง Hostless กับ Aiven โดยไม่ cache วันที่สังเกตการณ์หรือข้าม validation

## ตรวจโดยไม่รันเซิร์ฟเวอร์ในเครื่องผู้ใช้

Unit tests ตรวจ TLS/CA, configuration ที่ไม่ปลอดภัย, process exit/shutdown และการไม่รายงานตัวเลขศูนย์แทนข้อมูลที่อ่านไม่ได้ การทดสอบ process ใช้ Python subprocess สั้น ๆ ไม่เปิด web/API/worker ในเครื่อง

`scripts/verify_hostless_runtime_ci.py` ปฏิเสธการรันนอก GitHub CI สร้าง image ใน CI ใช้ backends ชั่วคราวที่ปิด provider ทุกแหล่งแล้ว บังคับ CPU 0.3 / RAM 768 MiB / no swap / read-only root และ inject `PORT=8000` ตามพฤติกรรม Hostless จริง ตรวจหน้าเว็บ API admin gates SSE cgroup และ kill/restart โปรเซสจริง ตรวจ source checks เป็นศูนย์ก่อนจบ รายงาน artifact `heatshift-hostless-runtime-ci.json`

การตรวจเพิ่มคำขอเริ่มต้นพร้อมกัน 8 รายการขณะที่ SSE เปิดอยู่ภายใต้ cap เดียวกัน ใช้ HTTP เฉพาะ loopback ใน CI และ private Compose backends ที่จำกัดด้วย CI guards ไม่ใช้แทน TLS/ใบรับรอง/latency ของ Aiven–Hostless จริง หลังเปิดจริงต้องตรวจ certificate, source schedules/publication, bounded workload และ usage/quote จาก provider อีกครั้ง รวมถึงคิวและ backup/recovery ก่อน production release
