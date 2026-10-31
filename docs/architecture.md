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
| S3/MinIO | source/derived raster และ tile artifacts; database เก็บ URI/checksum/footprint เท่านั้น |

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

- `/api/map/features` ต้องรับ `bbox`, layer, version, zoom และ pagination/limit
- Raster ใช้ XYZ/COG tile service ตาม provider/storage capability; ห้ามส่ง raster Bangkok ทั้งก้อนไป browser
- Vector ใช้ GeoJSON เฉพาะ viewport/pilot size; production scale ใช้ MVT
- Response ใส่ ETag/Cache-Control และ dataset version
- SSE event ระบุเฉพาะ affected layer IDs, area IDs, version และ observation metadata

## Reproducibility lineage

Optimization/scenario/forecast records ต้องเก็บ:

- input dataset version IDs
- priority formula/model version และ weights
- candidate/cost assumption version
- solver/model version + seed + constraint configuration
- created_at, created_by และ output uncertainty/limitations

เมื่อ active input เปลี่ยน ผลเดิมยังเปิดดูได้แต่มี `outdated=true`; ผู้ใช้เป็นคนสั่งคำนวณใหม่
