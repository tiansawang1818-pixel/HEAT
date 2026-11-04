# Data sources, freshness และ provider configuration

คำว่า “ล่าสุด” หมายถึง **latest available validated version ที่ connector ตรวจพบ** ไม่ได้หมายถึงการวัดแบบ real-time ทุก layer ต้องแสดงทั้ง `Last Source Observation` และ `Last System Check`

| Source | Product / purpose | Monitor cadence | Change key | Production credential / note |
|---|---|---:|---|---|
| Landsat 8/9 | USGS Collection 2 Level-2; LST | 24 h | scene/asset ID + publish time | Google Earth Engine project/service account; ใช้ scale/offset และ QA mask ตาม product docs |
| Sentinel-2 | Surface reflectance; NDVI | 6 h | asset ID + sensing time | GEE; mask cloud/shadow and retain clear coverage |
| Dynamic World | land-cover probabilities | 6 h | asset ID + model version | GEE; preserve probability/confidence, not only argmax class |
| WorldPop | population estimate | 30 d | release/version + reference year/checksum | ไม่ใช่ real-time; verify license and product metadata |
| Open-Meteo | observation/model/forecast context | 1 h | reference/model/run time | Usually no key; respect fair-use/rate limit and preserve model metadata |
| OSM / Overture | roads, buildings, public spaces | 7 d | replication sequence/release ID | Prefer approved extracts/incremental diffs; Overpass is not bulk production pipeline |
| Bangkok district boundaries | BMA / data.go.th official 50-district Shapefile | 7 d | CKAN resource ID + resource modification time + SHA-256 | Provider metadata currently does not declare a licence; preserve attribution and review reuse terms before public redistribution |

## Required environment

```dotenv
GEE_PROJECT=your-project
GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/gee-service-account.json
GEE_MAX_CONCURRENT_EXPORTS=2
OPEN_METEO_BASE_URL=https://api.open-meteo.com/v1/forecast
OVERPASS_API_URL=https://overpass-api.de/api/interpreter
OVERPASS_TIMEOUT_SECONDS=120
OVERPASS_MAX_RESPONSE_BYTES=20000000
OVERPASS_MAX_ELEMENTS=20000
OVERPASS_GRID_DIVISIONS=2
WORLDPOP_API_BASE_URL=https://api.worldpop.org/v3
WORLDPOP_API_KEY=
WORLDPOP_REFERENCE_YEAR=
BMA_BOUNDARY_CATALOG_URL=https://data.go.th/api/3/action/package_show
BMA_BOUNDARY_DATASET_ID=e537025b-1cf6-4c5b-8e46-c2e976f13283
BMA_BOUNDARY_RESOURCE_ID=d7be7e84-9d84-4595-bf8f-79b0bc01f1ae
BMA_BOUNDARY_MAX_DOWNLOAD_BYTES=10000000
OVERTURE_RELEASE=
```

Mount Google service-account JSON as a Docker/Kubernetes secret; do not copy it into the image or repository. Production should prefer workload identity where supported.

## Validation gates

### Bangkok administrative boundaries

- ตรวจ CKAN metadata ของชุด `e537025b-1cf6-4c5b-8e46-c2e976f13283` และ resource ID ที่ตรึงไว้ก่อนดาวน์โหลด
- รับเฉพาะ HTTPS จาก `data.bangkok.go.th`, จำกัดขนาด ZIP/member และอ่าน archive ในหน่วยความจำโดยไม่ extract path ลง filesystem
- อ่าน Shapefile encoding CP874, แปลง `EPSG:32647` เป็น `EPSG:4326`, repair geometry และตรวจว่าครบ 50 district codes/ชื่อไม่ซ้ำ
- คำนวณ SHA-256 จาก ZIP จริง เก็บ `DistrictBoundary` ทุกเวอร์ชัน และปิด `valid_to` ของขอบเขตก่อนหน้าแทนการลบประวัติ
- เมื่อมี demo area ชื่อเดียวกัน ระบบอัปเกรด record เดิมเป็น official boundary เพื่อไม่สร้างเขตซ้ำใน local database

ข้อมูลต้นทางระบุวันที่ resource ล่าสุดปี 2021 และ metadata package ปรับปรุงปี 2025 ระบบจะแสดง freshness ตามวันที่ resource จริง ไม่ใช้วันที่ ingest แทน และจะแสดงคำเตือนว่า provider metadata ไม่ได้ระบุ licence

### Satellite-derived layers

- asset footprint intersects configured Bangkok boundary
- CRS/affine transform and pixel size are declared and sane
- required bands and QA bands exist
- scale factor/offset match the exact collection version
- cloud/shadow/snow/saturation mask applied
- valid coverage exceeds configured threshold; partial coverage remains explicit
- computed value distribution falls within scientifically plausible configured bounds
- output checksum, processor version and source asset ID recorded

### Implemented Earth Engine regional processors

- Landsat 8/9: ตัด cloud/cirrus/cloud shadow/snow ด้วย `QA_PIXEL` bits 1–4, ตัด saturated pixels ด้วย `QA_RADSAT`, ใช้ Collection 2 Level-2 scale/offset ของ `ST_B10`, แล้วบันทึก district mean, p90 และ valid-pixel coverage
- Sentinel-2: ตัด SCL classes 3, 8, 9, 10, 11 แล้วคำนวณ mean NDVI พร้อม valid-pixel coverage
- Dynamic World: เก็บ mean tree/built probability และ mean maximum-class probability เป็น confidence; ไม่แปลง argmax label ให้เป็นความจริงเด็ดขาด
- Regional record จะถูกเขียนพร้อม source asset ID และ `processor_version=gee-zonal-v1` เฉพาะเมื่อทั้ง asset validation และ processed-value validation ผ่าน ระบบจะคำนวณ Heat Exposure Priority ใหม่และส่ง affected-layer event หลัง publication สำเร็จ

พื้นที่ที่มี valid coverage ต่ำกว่า 5% จะไม่ถูกเผยแพร่จาก observation นั้น; ช่วง 5–25% จะติดสถานะ `limited_coverage` แทนการปกปิดข้อจำกัดของเมฆ/ข้อมูลขาดหาย

### Population

- reference year and unit (`persons/pixel`, density, count) preserved
- raster sum/aggregation method respects source grid and avoids double counting
- no interpolation is described as a current observation

WorldPop connector ใช้ Global2 `/catalog` เพื่อตรวจ release/ปีที่มีอยู่ และ `/batch` เพื่อรวมประชากรของ Bangkok geographic areas ในคำขอเดียว ค่าเริ่มต้นเลือกปีล่าสุดที่ไม่เกินปีปัจจุบัน จึงไม่หยิบประมาณการอนาคตมาแสดงเป็นข้อมูลปัจจุบันโดยอัตโนมัติ สามารถตรึงปีด้วย `WORLDPOP_REFERENCE_YEAR` ได้ ทุกผลลัพธ์ใช้สถานะ `model_estimate`, เก็บ reference year/release/resolution/citation และไม่สร้าง uncertainty ที่ provider ไม่ได้ส่งมา

Anonymous API มี quota ต่ำกว่า API key ระบบบันทึก `X-RateLimit-*` ลง source check และ Celery เคารพ `Retry-After` เมื่อได้รับ HTTP 429 ห้ามใส่ key ลง repository หรือ frontend environment

### Vector candidates

- ตัวตรวจรายสัปดาห์อ่าน OSM daily replication state ก่อน แล้วแบ่ง Bangkok bbox เป็น grid ย่อยเพื่อ query Overpass สำหรับตลาด, สถานพยาบาล, community centres, parks, playgrounds และ squares จากนั้น deduplicate ด้วย OSM type/ID ชุด pilot จงใจไม่ bulk-query ป้ายรถโดยสาร/โรงเรียนทั้งหมดบน public endpoint
- ระบบคำนวณ checksum จาก OSM IDs, tags, intervention screening type และตำแหน่งจริง ถ้า snapshot ไม่เปลี่ยนจะไม่สร้าง dataset version หรือประมวลผลซ้ำ
- node ใช้พิกัดจริง ส่วน way/relation ใช้ `out center` สำหรับตำแหน่งคัดกรอง จึงไม่อ้างว่าเป็นขอบเขตแปลงหรือพื้นที่ก่อสร้าง
- จุดต้องอยู่ในขอบเขต 50 เขตที่ publish แล้ว, ID ต้องไม่ซ้ำ, และเก็บลิงก์ feature/ODbL/`© OpenStreetMap contributors` ทุก record
- candidate ใหม่มี `estimated_cost_thb=null`, `estimated_beneficiaries=null`, `feasibility_status=insufficient_evidence` และข้อกำหนดให้ตรวจสิทธิในที่ดิน/หน้างาน/การออกแบบ จึงไม่เข้า OR-Tools โดยอัตโนมัติ
- เมื่อ snapshot เปลี่ยน ระบบ upsert stable IDs และทำ candidate ที่หายไปเป็น inactive โดยไม่ลบประวัติ การ publish ใหม่ทำให้ optimization run เดิมถูกทำเครื่องหมาย outdated
- public Overpass เหมาะกับ pilot query ที่จำกัดขอบเขต ไม่ใช่ bulk production pipeline; deployment ขนาดใหญ่ควรเปลี่ยนเป็น extract/replication diff ที่ผู้ให้บริการอนุมัติ

## Freshness status policy

Thresholds are configuration, not universal truth. Recommended initial policy:

- `up_to_date`: latest discovered asset has a published valid version and age ≤ threshold
- `updating`: newer asset is staged/processing while previous valid layer remains visible
- `stale`: last valid observation/check is older than the layer-specific threshold; show exact reason
- `unavailable`: no valid published dataset or provider cannot be configured
- `error`: latest check/ingestion failed; show previous valid data separately if present

Provider outage must not rewrite `last_source_observed_at`. System check time is set after each real check attempt with outcome and latency.

## Basemap

Local default uses the CARTO dark-matter style URL. Confirm service terms and traffic limits for the target deployment, preserve visible attribution, and replace it with an organization-approved licensed tile provider for production traffic.
