# Operations runbook

## Health and service checks

```bash
docker compose ps
curl -fsS http://localhost:8000/api/health
curl -fsS http://localhost:8000/api/health/live
curl -fsS http://localhost:8000/api/health/ready
curl -fsS http://localhost:8000/api/metrics
curl -fsS http://localhost:8000/api/datasets/status
curl -N http://localhost:8000/api/events/stream
docker compose logs --tail=200 api worker beat
```

`api/health` ตอบได้ไม่ได้แปลว่า provider ทุกตัวพร้อม ให้ใช้ Data Observatory ตรวจ connectivity, latest check, queue และ quality แยก source

- `health/live` ตรวจว่า process ยังตอบสนอง โดยไม่ผูกกับ provider ภายนอก
- `health/ready` ตรวจ database และ source configuration; load balancer ควรถอด instance ที่ตอบ 503
- `metrics` เป็น Prometheus text format จาก durable pipeline state และ process-local HTTP counters โดยไม่ส่ง error message/provider payload

เปิด local Prometheus ด้วย `make observability` แล้วดูที่ <http://localhost:9090> config จะอ่าน `infra/monitoring/alerts.yml` ซึ่งเฝ้า API down, source check เกินเวลา, data stale, validation failure และ job ค้าง Prometheus image ตรึงที่ stable release; ก่อน production upgrade ให้ตรวจ release notes และ digest จากแหล่งทางการ

## Manual refresh

Manual refresh เป็น admin operation และ enqueue background job จริง:

```bash
curl -fsS -X POST http://localhost:8000/api/ingestion/run \
  -H "Content-Type: application/json" \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -d '{"source":"open_meteo"}'
```

บันทึก job ID แล้วติดตามผ่าน `/api/ingestion/status?job_id=...` หรือ Data Observatory อย่ากดซ้ำเมื่อ job ยังถือ distributed lock

## Provider outage / rate limit

1. ตรวจ error class และ provider response headers; ห้าม log credential/token
2. `429`, timeout และ `5xx` ใช้ exponential backoff + jitter และ honor `Retry-After`
3. เปิด circuit/ลด cadence หาก failure ต่อเนื่อง; ห้ามวน query ราคาแพง
4. คง published version เดิมและเปลี่ยน status/reason ให้ผู้ใช้เห็น
5. ตรวจ quota dashboard และ checkpoint ก่อน requeue
6. เมื่อ provider กลับมา เริ่มจาก monitor/checkpoint ไม่บังคับ full backfill เว้นมีแผนอนุมัติ

## Failed validation

1. ห้าม promote staged artifact หรือเปลี่ยน active layer pointer
2. เปิด validation report: CRS, footprint, band, coverage, range, checksum
3. เปรียบเทียบ collection/product version กับ processor config
4. หากแก้ processor ให้ bump processor version; rerun ด้วย idempotency key ใหม่
5. เก็บ reject record ตาม retention policy เพื่อ audit แต่ลบ temporary binary ตาม policy

Future-outlook validation gates ปรับได้ด้วย `FORECAST_MIN_YEARS`, `FORECAST_MIN_AREAS`, `FORECAST_MIN_VALIDATION_SAMPLES` และ `FORECAST_MIN_BASELINE_IMPROVEMENT` การลดค่าเหล่านี้เป็น scientific policy change ต้องผ่าน review และบันทึกเหตุผล ห้ามลดเพื่อทำให้ UI มี projection เท่านั้น

## Queue backlog

- ตรวจ scheduled vs active/reserved tasks, lock TTL, worker memory และ provider latency
- scale worker ตาม task class; raster jobs ไม่ควรแย่ง queue ของ status/notification
- revoke เฉพาะ job ที่พิสูจน์แล้วว่าค้าง และต้องแน่ใจว่า task รองรับ safe retry
- หาก publication commit สำเร็จแต่ event ล้มเหลว ให้ replay outbox ห้าม publish ซ้ำ

## Database / object storage

- backup Postgres และ bucket versioning/retention ต้องมีรอบที่สอดคล้องกัน
- ทดสอบ restore เป็นประจำ ไม่ใช่เพียงตรวจว่ามี backup file
- raster metadata URI/checksum ต้องตรวจได้หลัง restore
- migration ใช้ `alembic upgrade head` และสำรองก่อน destructive migration

สำหรับ local Compose ใช้ `make backup` เพื่อสร้าง PostgreSQL custom-format dump และ SHA-256 ใน `BACKUP_DIR` (ค่าเริ่มต้น `./backups`) สคริปต์ไม่ลบ backup เก่าอัตโนมัติ การ restore ต้องทำใน environment แยกก่อน ตรวจ checksum แล้วใช้ `pg_restore --list` เพื่อตรวจ manifest จากนั้นจึงซ้อม restore ตาม runbook ขององค์กร ห้ามถือว่า backup สำเร็จจนกว่าจะ restore และตรวจ row counts/checksums ได้

Postgres dump ไม่รวม object storage ให้เปิด bucket versioning/replication และสำรอง raster/object keys ไปยัง account หรือ region แยก โดยกำหนด retention ให้สอดคล้องกับ database backup และทดสอบว่า `RasterAssetMetadata.storage_uri` ทุก URI ยังอ่านได้หลัง restore

## Release checklist

1. tests + migration rehearsal ผ่าน และ `make readiness` จบด้วย exit code 0 (ผ่านการตรวจ configuration/dependencies เบื้องต้น)
2. ไม่มี demo mode/fixture ingestion ใน production
3. provider terms/quota/attribution ผ่าน review
4. OIDC admin auth, CORS, TLS, secrets และ Redis rate limits พร้อม (`RATE_LIMIT_ENABLED=true`)
5. check update/rollback สำหรับ database + artifact formats
6. smoke API, map tiles, SSE/polling และหนึ่ง read-only provider check

`GET /api/system/readiness?probe_dependencies=true` ใช้ตรวจรายการเดียวกับหน้า Data Observatory และ CLI โดย probe PostGIS/Redis แบบ timeout สั้น พร้อมตรวจ configuration ของ OIDC, CORS, rate limit และ Earth Engine ผลลัพธ์ `configuration_ready` ระบุเฉพาะขอบเขต `configuration_and_dependencies` ไม่ใช่การรับรอง production ทั้งระบบ การข้าม Redis probe จะยังติด blocker ผลลัพธ์ไม่คืนค่า secret, token, endpoint URL หรือ credential path สถานะ Earth Engine identity เป็นเพียง configuration hint; ต้องยืนยันสิทธิ์จริงด้วย source check, worker/Beat, frontend demo mode และ ingestion-to-map ก่อน release ทุกครั้ง
