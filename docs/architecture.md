# สถาปัตยกรรมและวงจรชีวิตข้อมูล

## หลักการ

1. **Published version is immutable** — observation ที่ publish แล้วไม่ถูกเขียนทับ การแก้ไขคือ version ใหม่
2. **Provenance first** — ทุก indicator ชี้กลับไปยัง source asset, processing code/model version และ validation report
3. **Incremental by default** — monitor ใช้ asset ID, version, timestamp หรือ checksum เพื่อ enqueue เฉพาะสิ่งที่เปลี่ยน
4. **Safe publication** — transaction เปลี่ยน active layer หลัง validation/processing สำเร็จครบเท่านั้น
5. **Map stays usable** — frontend รักษา center, zoom, layer selection และ selected area ระหว่างรับ event

## Components

| Component | Responsibility |
|---|---|
| Next.js web | Thai command-center UX, MapLibre/deck.gl, charts, optimizer/scenario forms, SSE + polling fallback |
| FastAPI | REST API, OpenAPI, spatial queries, admin authorization, SSE stream |
| Celery Beat | สร้าง monitor jobs ตาม cadence โดยไม่สมมติว่ามี asset ใหม่ |
| Celery workers | discovery, download/export, validation, zonal analytics, publication |
| Redis | durable-enough broker/result backend for local deployment, locks, event fan-out/cache |
| PostgreSQL/PostGIS | metadata, geometry, versioned observations, scores, candidates, optimization lineage |
| S3/MinIO | source/derived raster artifacts ที่ต้องถือครองเอง; database เก็บ URI/checksum/footprint เท่านั้น ส่วน GEE raster ใช้ virtual provider URI |

Production ควรใช้ managed Redis ที่มี persistence/HA หรือ broker ที่ตรง SLO; local Compose ใช้ Redis AOF เพื่อความสะดวก ไม่ใช่ข้ออ้างว่าไม่มีโอกาสสูญเสีย message

## Pipeline state machine

```text
scheduled → checking → no_change
                     ↘ discovered → queued → staging → validating
                                                     ↘ rejected
                                                      ↘ processing → publishing → published
                                                                     ↘ failed
```

- `no_change` ต้องอัปเดต `last_checked_at` แต่ห้ามเปลี่ยน `last_source_observed_at`
- `rejected/failed` ต้องเก็บ report และปล่อย active published version เดิมไว้
- ทุก task ใช้ idempotency key เช่น `{source}:{asset_id}:{processor_version}`
- distributed lock ต้องมี owner token + expiry และ release เฉพาะ owner
- retry เฉพาะ transient error ด้วย exponential backoff + jitter; validation failure ไม่ควรถูก retry โดยอัตโนมัติ
- Production ใช้ Redis lock เท่านั้นและหยุดเริ่ม monitor เมื่อ lock backend ล่ม ค่า lease 65 นาทีมากกว่า Celery hard deadline 60 นาที; local fallback ใช้เฉพาะ development/test
- เมื่อ monitor ใหม่ได้ exclusive source lock จะกู้ unfinished dataset versions เป็น retryable processing error และปิด audit jobs/checks ที่ worker เดิมทำไม่เสร็จ transaction การเผยแพร่ทำให้ observation/event ที่ยังไม่ commit ถูก rollback และ retry ใช้ version ID เดิม

## Publication transaction

1. เขียน staged metadata และ artifact checksum
2. ตรวจ CRS, footprint, band/value range, nodata/cloud coverage และ geometry validity
3. สร้าง derived artifacts ด้วย temporary key
4. คำนวณเฉพาะพื้นที่ที่ intersect footprint
5. ใน database transaction: mark dataset version `published`, increment `layer_version`, update active pointer, append `pipeline_event`
6. หลัง commit: invalidate cache keys ตาม layer/area และ emit event
7. Client fetch layer metadata/features version ใหม่; tile URL มี version เพื่อหลีกเลี่ยง stale cache

หากขั้นที่ 6 ล้มเหลว ให้ outbox/event replay ส่งซ้ำโดยไม่ publish dataset ซ้ำ

## API and map loading

- `/api/map/features` รับ `bbox`, layer/version และ limit; production PostGIS ใช้ `ST_Intersects(ST_MakeEnvelope(...))` กับ GiST index ก่อนอ่าน geometry ส่วน SQLite fixture ใช้ application bounds fallback
- Raster ที่ publish จาก Earth Engine เก็บ immutable source asset/version และ virtual raster metadata แล้ว `/api/map/tiles/{layer}` สร้าง provider-backed XYZ descriptor จาก processing recipe เดียวกับ regional analytics
- Frontend โหลดเฉพาะ tiles ใน viewport ด้วย MapLibre และเก็บ district GeoJSON เป็น fallback/click target; ห้ามส่ง raster Bangkok ทั้งก้อนไป browser
- Earth Engine map token มีอายุจำกัด ระบบ cache descriptor ใน memory ตาม `GEE_TILE_DESCRIPTOR_TTL_SECONDS`, ตอบ `Cache-Control: private` และไม่บันทึก token ลงฐานข้อมูล
- Vector ใช้ GeoJSON เฉพาะ viewport/pilot size; production scale ใช้ MVT
- MapLibre ส่ง bounds หลัง load/moveend; client debounce 180 ms, abort request เก่า และคง center/zoom ระหว่าง SSE update การค้นหาเขตใช้ directory endpoint ที่ไม่มี boundary geometry แล้วโหลด geometry เฉพาะเมื่อเลือก
- Response GeoJSON ใส่ content-derived ETag และ private `Cache-Control`; URL มี layer/refresh version ทำให้กลับไป viewport เดิมใช้ browser cache ได้โดยไม่เสี่ยงค้างหลัง publication event
- SSE event ระบุเฉพาะ affected layer IDs, area IDs, version และ observation metadata
- SSE เริ่มจาก event ล่าสุดเพื่อไม่ replay ประวัติทั้งระบบ รองรับ `Last-Event-ID` และเรียง `(created_at, id)` เพื่อ reconnect โดยไม่ข้าม event ที่มี timestamp เดียวกัน Client refresh metadata ก่อนและขอ feature ใหม่เฉพาะเมื่อ active layer อยู่ใน `affected_layers`

## Reproducibility lineage

`/api/map/observations` แสดงเฉพาะ published snapshots ของชั้นข้อมูลต้นทาง การเลือก `dataset_version_id` จะตรึง map features, raster descriptor, area indicators และ history cutoff เป็นเวอร์ชันเดียวกัน โดยปฏิเสธ staging/wrong-source IDs ข้อมูลล่าสุดเลือกจาก source observation time ก่อน publication time เพื่อให้ late backfill คงเป็นข้อมูลย้อนหลัง สูตรคะแนนใช้ active source versions เดียวกันและไม่นำค่าจากคนละ snapshot มาปะปนเมื่อ coverage ขาดหาย

Optimization/scenario/forecast records ต้องเก็บ:

- input dataset version IDs
- priority formula/model version และ weights
- candidate/cost assumption version
- solver/model version + seed + constraint configuration
- created_at, created_by และ output uncertainty/limitations

เมื่อ active input เปลี่ยน ผลเดิมยังเปิดดูได้แต่มี `outdated=true`; ผู้ใช้เป็นคนสั่งคำนวณใหม่
