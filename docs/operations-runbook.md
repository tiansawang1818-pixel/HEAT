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

`make observability` ประเมิน rules ในเครื่องเท่านั้น สำหรับการส่งแจ้งเตือนจริง ใช้ `docker-compose.monitoring.yml` ซึ่งต่อ Prometheus เข้า Alertmanager และเก็บสถานะ deduplication ใน persistent volume คัดลอก `infra/monitoring/alertmanager.example.yml` ไปไว้ใน `secrets/alertmanager.yml`, ใส่ receiver HTTPS ที่องค์กรอนุมัติ และให้ service UID/GID 65534 อ่านได้โดยไม่เปิดไฟล์ต่อผู้ใช้อื่น จากนั้นรัน:

```bash
ALERTMANAGER_CONFIG_FILE=./secrets/alertmanager.yml make monitoring-up
```

preflight ปฏิเสธ receiver ตัวอย่างและ HTTP ที่ออกจาก production โดยไม่พิมพ์ URL หรือรหัสรับ webhook จากนั้น `amtool` ตรวจโครงสร้าง config จึงเริ่มบริการ พอร์ต Alertmanager ไม่เปิดสาธารณะ ต้องยืนยันการรับ alert และ resolved notification ที่ปลายทางจริงก่อนเปิดระบบ CI ใช้ receiver จำลองภายใน Docker network และไม่ส่งข้อความถึงบุคคลหรือระบบภายนอก

Control worker บันทึก heartbeat ใน Redis ที่มี TTL หากหายหรือเกิน 3 นาที Prometheus จะเตือนหลัง grace period แยกจากเวลาตรวจข้อมูลรายชั่วโมง/รายสัปดาห์ แหล่งข้อมูลที่ไม่เคยตรวจมี age นับจาก source configuration เพื่อไม่ให้หายจากการเฝ้าระวัง

## Manual refresh

Manual refresh เป็น admin operation และ enqueue background job จริง:

```bash
curl -fsS -X POST http://localhost:8000/api/ingestion/run \
  -H "Content-Type: application/json" \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -d '{"source":"open-meteo"}'
```

บันทึก job ID แล้วติดตามผ่าน `/api/ingestion/status?job_id=...` หรือ Data Observatory อย่ากดซ้ำเมื่อ job ยังถือ distributed lock

งาน manual ผ่าน Celery ใช้ job ID เดิมตลอด retry สูงสุด 5 ครั้งหลังครั้งแรก transient errors ใช้ exponential backoff + jitter ส่วน rate limit เคารพ `Retry-After` สถานะระหว่างรอคือ `queued/retry_scheduled` และ `checkpoint.next_retry_at` บอกเวลาลองใหม่ การส่งซ้ำหลัง job สำเร็จจะคืนผลที่บันทึกไว้ การใช้ API background fallback มีเฉพาะ development และไม่มี durable retry

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

Beat ส่ง heartbeat ไป queue `control` ทุก 60 วินาที โดย `dispatcher` worker แยกจาก ETL worker งานค้นหา provider รอบแรกจึงไม่รอครบ 1 ชั่วโมง/1 สัปดาห์ ระบบอ่าน cadence จาก `data_sources` และเวลาตรวจจริงในฐานข้อมูล การ restart ไม่ทำให้เริ่มนับรอบใหม่ งาน geospatial รอขอบเขตทางการที่ผ่าน validation; Open-Meteo ตรวจได้อิสระ งานที่ถูกส่งเข้าคิวมี job ID ถาวรและไม่ถูกส่งซ้ำก่อน lease หมด หาก process หยุดระหว่างบันทึกกับส่งคิว ระบบ replay ID เดิมและใช้ job/source locks ป้องกันประมวลผลซ้ำ

- ตรวจ scheduled vs active/reserved tasks, lock TTL, worker memory และ provider latency
- scale worker ตาม task class; raster jobs ไม่ควรแย่ง queue ของ status/notification
- revoke เฉพาะ job ที่พิสูจน์แล้วว่าค้าง และต้องแน่ใจว่า task รองรับ safe retry
- หาก publication commit สำเร็จแต่ event ล้มเหลว ให้ replay outbox ห้าม publish ซ้ำ

Production ต้องได้ Redis lock ก่อนเริ่ม source check; Redis ขัดข้องจะให้ retry แทนการใช้ process-local lock worker มี soft deadline 50 นาที, hard deadline 60 นาที และ lock lease 65 นาที หลัง worker หยุดกลางทาง monitor ถัดไปที่ได้ exclusive lock จะทำเครื่องหมาย check/job เดิมเป็น interrupted และให้ asset version ที่ยังไม่ publish เข้า safe retry โดยใช้ version ID เดิม Published observations และ publication events ไม่ถูกเขียนซ้ำ

## Database / object storage

- backup Postgres และ bucket versioning/retention ต้องมีรอบที่สอดคล้องกัน
- ทดสอบ restore เป็นประจำ ไม่ใช่เพียงตรวจว่ามี backup file
- raster metadata URI/checksum ต้องตรวจได้หลัง restore
- migration ใช้ `alembic upgrade head` และสำรองก่อน destructive migration

สำหรับ local Compose ใช้ `make backup` เพื่อสร้าง PostgreSQL custom-format dump และ SHA-256 ใน `BACKUP_DIR` (ค่าเริ่มต้น `./backups`) สคริปต์ไม่ลบ backup เก่าอัตโนมัติ การ restore ต้องทำใน environment แยกก่อน ตรวจ checksum แล้วใช้ `pg_restore --list` เพื่อตรวจ manifest จากนั้นจึงซ้อม restore ตาม runbook ขององค์กร ห้ามถือว่า backup สำเร็จจนกว่าจะ restore และตรวจ row counts/checksums ได้

Postgres dump ไม่รวม object storage ให้เปิด bucket versioning/replication และสำรอง raster/object keys ไปยัง account หรือ region แยก โดยกำหนด retention ให้สอดคล้องกับ database backup และทดสอบว่า `RasterAssetMetadata.storage_uri` ทุก URI ยังอ่านได้หลัง restore

มี `infra/systemd/heatshift-backup.service` และ `.timer` สำหรับ Linux server ที่ติดตั้งโครงการใน `/opt/heatshift` รอบสำรองคือประมาณ 02:30 น. Asia/Bangkok พร้อม jitter 15 นาที และทำรอบที่พลาดเมื่อเครื่องกลับมาเปิด ให้ผู้ดูแลติดตั้ง unit, สร้าง service user ที่มีสิทธิ์ใช้ Docker ตามนโยบายองค์กร, เตรียม directory และ `/etc/heatshift/backup.env` จากตัวอย่าง แล้วจึง enable timer ไฟล์เหล่านี้ยังไม่ได้ติดตั้งหรือทำงานบนเครื่องผู้ใช้ การมี timer ไม่แทนการเข้ารหัส ส่งสำรองออกนอกเครื่อง กำหนด retention และซ้อมกู้คืนจากปลายทางจริง

backup helper อ่านชื่อฐานข้อมูล/ผู้ใช้จาก container โดยตรงและตั้งชื่อ dump แบบไม่ชนกันเมื่อมีการสั่งใกล้เวลาเดียวกัน CI รัน helper กับ release containers และตรวจ checksum เพิ่มเติมจากการซ้อม restore ไปยังฐานข้อมูลแยก

### Encrypted remote backup

`make prepare-backup` สร้างรหัสสุ่มใน `secrets/restic-password` แบบ 0600 และไม่เขียนทับไฟล์เดิม ไม่เปิด storage และไม่ส่งข้อมูลออกไป ให้ย้ายรหัสไปเก็บใน secret manager แยกจากเซิร์ฟเวอร์ด้วย ผู้ที่ไม่มีรหัสนี้จะกู้คืน repository ไม่ได้ ห้ามนำไฟล์เข้า Git หรือ image

ติดตั้ง Restic จากแหล่งทางการหรือ package repository ของระบบ เตรียม S3-compatible bucket ใน account/เครื่องแยก เปิด retention/object-lock ตามนโยบาย แล้วตั้ง `RESTIC_REPOSITORY=s3:https://<approved-endpoint>/<bucket>` และ `RESTIC_PASSWORD_FILE` เป็นไฟล์ของ service user แบบ 0600 พร้อม credentials ที่อนุญาตให้อ่าน/เขียน/ตรวจรายการใน bucket สำรองเท่านั้น กำหนด credentials ฝั่ง host; ไม่ส่งให้ API/web containers จากนั้น initialize repository ด้วย `restic init` เพียงครั้งแรกโดยใช้ค่าเหล่านี้

`HEATSHIFT_COMPOSE_ENV=production make backup-offsite` ตรวจ repository เดิม สร้าง dump ที่อ่านตรวจแล้ว เข้ารหัส/ส่งผ่าน Restic และดาวน์โหลด snapshot ID เดิมกลับมาถอดรหัสใน temporary directory เปรียบเทียบ SHA-256 แล้วตรวจ `pg_restore --list` โดยไม่เขียนฐานข้อมูลจริง จึงคืน `status=verified` หากขั้นตอนไหนล้มเหลวจะคืน exit code 2 พร้อมเหตุผลที่ไม่เปิดเผย endpoint/credentials ไม่มีคำสั่ง `forget`, `prune` หรือการลบ snapshot โดยอัตโนมัติ ดู [Restic repository setup](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html), [backup](https://restic.readthedocs.io/en/stable/040_backup.html) และ [restore](https://restic.readthedocs.io/en/stable/050_restore.html)

มี `infra/systemd/heatshift-backup-offsite.service`/`.timer` และตัวอย่าง private env สำหรับ Linux รอบประมาณ 03:15 น. Asia/Bangkok พร้อม jitter 15 นาที ใช้ lock ร่วมที่ `/var/lib/heatshift-backup/offsite.lock` เพื่อไม่ให้ทำซ้อน งานคืนข้อมูลลง directory ส่วนตัวใหม่และล้าง temporary files เมื่อจบตามปกติ; ต้องมีพื้นที่ว่างพอสำหรับ dump กับไฟล์กู้คืนและมีนโยบาย cleanup หลังเครื่องดับ/ถูก kill ไม่มีการติดตั้ง timer หรือส่งข้อมูลออกจากเครื่องนี้โดยอัตโนมัติ ผู้ดูแลต้องต่อ failure alert ของ unit เข้าระบบปฏิบัติการจริง

CI ใช้ repository เข้ารหัสใน temporary storage ที่แยกจากฐานข้อมูล และตรวจการดาวน์โหลด/ถอดรหัส/อ่าน archive จริง โดยอนุญาต local repository เฉพาะ `CI=true`, `ENVIRONMENT=test` และ `--allow-local-test` ผลมี scope ชัดเจนและไม่รับรองว่าเป็น off-host storage จริง ต้องตรวจสถานที่เก็บ สิทธิ์ account และซ้อม SQL restore ใน environment แยกจากปลายทางจริงก่อนเปิดระบบ

## Release checklist

1. tests + migration rehearsal ผ่าน และ `make readiness` จบด้วย exit code 0 (ผ่านการตรวจ configuration/dependencies เบื้องต้น)
2. ไม่มี demo mode/fixture ingestion ใน production
3. provider terms/quota/attribution ผ่าน review
4. OIDC admin auth, CORS, TLS, secrets และ Redis rate limits พร้อม (`RATE_LIMIT_ENABLED=true`)
5. check update/rollback สำหรับ database + artifact formats
6. smoke API, map tiles, SSE/polling และหนึ่ง read-only provider check

`GET /api/system/readiness?probe_dependencies=true` ใช้ตรวจรายการเดียวกับหน้า Data Observatory และ CLI โดย probe PostGIS/Redis แบบ timeout สั้น พร้อมตรวจ configuration ของ OIDC, CORS, rate limit และ Earth Engine ผลลัพธ์ `configuration_ready` ระบุเฉพาะขอบเขต `configuration_and_dependencies` ไม่ใช่การรับรอง production ทั้งระบบ การข้าม Redis probe จะยังติด blocker ผลลัพธ์ไม่คืนค่า secret, token, endpoint URL หรือ credential path สถานะ Earth Engine identity เป็นเพียง configuration hint; ต้องยืนยันสิทธิ์จริงด้วย source check, worker/Beat, frontend demo mode และ ingestion-to-map ก่อน release ทุกครั้ง

หน้า Data Observatory มีรายงานภาษาไทยจาก `GET /api/system/data-readiness` แยกอีกชุด โดยตรวจเวอร์ชันที่เผยแพร่ รายงาน validation พื้นที่ที่ข้อมูลครบ และหลักฐานโครงการ พร้อมแสดงรายการที่ต้องยืนยันภายนอก หน้าเว็บอ่านผลใหม่ทุก 60 วินาทีโดยไม่ reload ทั้งหน้า ผลแบบจำลองอนาคตจะถือว่าพร้อมตรวจสอบเฉพาะเมื่อประวัติและนโยบาย validation ตรงกับปัจจุบัน; ข้อมูลย้อนหลังที่เพิ่มเข้ามาทำให้ผลเดิมหมดความเป็นปัจจุบัน แต่ไม่แก้ไขผลที่บันทึกไว้
