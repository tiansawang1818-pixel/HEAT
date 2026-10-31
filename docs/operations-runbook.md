# Operations runbook

## Health and service checks

```bash
docker compose ps
curl -fsS http://localhost:8000/api/health
curl -fsS http://localhost:8000/api/datasets/status
curl -N http://localhost:8000/api/events/stream
docker compose logs --tail=200 api worker beat
```

`api/health` ตอบได้ไม่ได้แปลว่า provider ทุกตัวพร้อม ให้ใช้ Data Observatory ตรวจ connectivity, latest check, queue และ quality แยก source

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

## Release checklist

1. tests + migration rehearsal ผ่าน
2. ไม่มี demo mode/fixture ingestion ใน production
3. provider terms/quota/attribution ผ่าน review
4. admin auth, CORS, TLS, secrets และ rate limits พร้อม
5. check update/rollback สำหรับ database + artifact formats
6. smoke API, map tiles, SSE/polling และหนึ่ง read-only provider check
