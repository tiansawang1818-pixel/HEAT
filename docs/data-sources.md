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
| Bangkok district boundaries | BMA / data.go.th official 50-district Shapefile | 7 d | CKAN resource ID + resource modification time + SHA-256 | The BMA origin catalog does not declare a license. The bundled initial snapshot comes from the data.go.th Creative Commons Attribution entry. |

## Required environment

```dotenv
GEE_PROJECT=your-project
GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/gee-service-account.json
GEE_MAX_CONCURRENT_EXPORTS=2
GEE_TILE_DESCRIPTOR_TTL_SECONDS=3000
OPEN_METEO_BASE_URL=https://api.open-meteo.com/v1/forecast
OVERPASS_API_URL=https://overpass-api.de/api/interpreter
OVERPASS_TIMEOUT_SECONDS=120
OVERPASS_MAX_RESPONSE_BYTES=20000000
OVERPASS_MAX_ELEMENTS=20000
OVERPASS_GRID_DIVISIONS=2
WORLDPOP_API_BASE_URL=https://api.worldpop.org/v3
WORLDPOP_API_KEY=
WORLDPOP_REFERENCE_YEAR=
BMA_BOUNDARY_CATALOG_URL=https://data.bangkok.go.th/api/3/action/package_show
BMA_BOUNDARY_DATASET_ID=e537025b-1cf6-4c5b-8e46-c2e976f13283
BMA_BOUNDARY_RESOURCE_ID=d7be7e84-9d84-4595-bf8f-79b0bc01f1ae
BMA_BOUNDARY_MAX_DOWNLOAD_BYTES=10000000
OVERTURE_RELEASE=
```

Mount Google service-account JSON as a Docker/Kubernetes secret; do not copy it into the image or repository. Production should prefer workload identity where supported.

For the combined Hostless runtime, set `GEE_PROJECT` and inject `GEE_SERVICE_ACCOUNT_JSON_BASE64` as a write-only environment variable if mounted credentials are unavailable. The runtime removes the encoded value from child environments and gives Google ADC the path to a mode-0600 file. The connector scopes the credentials to Earth Engine before initialization. Use a service account dedicated to this project with the [Earth Engine API permissions](https://developers.google.com/earth-engine/guides/access_control#set_earth_engine_service_usage).

## Validation gates

### Bangkok administrative boundaries

- ตรวจ CKAN metadata ของชุด `e537025b-1cf6-4c5b-8e46-c2e976f13283` และ resource ID ที่ตรึงไว้ก่อนดาวน์โหลด
- ตรวจ CKAN API ของกรุงเทพมหานครทุก 7 วัน เมื่อยังไม่มีขอบเขตที่เผยแพร่และเชื่อมต่อแค็ตตาล็อกไม่ได้ ระบบสามารถตรวจและเผยแพร่สำเนาจาก [ชุดข้อมูล 50 เขตของกรุงเทพมหานคร](https://data.go.th/th/dataset/district) ที่ระบุ Creative Commons Attribution ดูข้อมูลกำกับใน `data/bootstrap/bma/manifest.json`
- รับเฉพาะ HTTPS จาก `data.bangkok.go.th`, จำกัดขนาด ZIP/member และอ่าน archive ในหน่วยความจำโดยไม่ extract path ลง filesystem
- ต้นทางระบุ format ว่า `shapfile`; เก็บค่านี้ตามจริง และรับ alias ของ shapefile เฉพาะ URL `.zip` บน host ที่อนุญาต จากนั้นตรวจ ZIP และสมาชิก `.shp/.shx/.dbf/.prj` จริงเหมือนเดิม
- อ่าน Shapefile encoding CP874, แปลง `EPSG:32647` เป็น `EPSG:4326`, repair geometry และตรวจว่าครบ 50 district codes/ชื่อไม่ซ้ำ
- คำนวณ SHA-256 จาก ZIP จริง เก็บ `DistrictBoundary` ทุกเวอร์ชัน และปิด `valid_to` ของขอบเขตก่อนหน้าแทนการลบประวัติ
- เมื่อมี demo area ชื่อเดียวกัน ระบบอัปเกรด record เดิมเป็น official boundary เพื่อไม่สร้างเขตซ้ำใน local database

ไฟล์สำเนาระบุวันที่เผยแพร่ resource ปี 2020 ส่วนไฟล์ที่ตรวจจากแค็ตตาล็อก กทม. ระบุปี 2021 และมี SHA-256 ตรงกัน ระบบเก็บวันที่ตามรายการที่ใช้จริง เมื่อเผยแพร่สำเนาจะระบุว่าการตรวจต้นทางยังไม่สำเร็จ การตรวจครั้งถัดไปยังเรียกต้นทางตามกำหนดและคงขอบเขตที่เผยแพร่ไว้หากต้นทางยังใช้ไม่ได้

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
- `gee-zonal-v2` คำนวณสัดส่วนพิกเซลที่ใช้ได้และค่าสถิติของเขตด้วย `reduceRegions` ตามลำดับ คำนวณค่าสถิติเฉพาะเขตที่ผ่านเกณฑ์ coverage และตรวจว่า Earth Engine คืนผลครบหนึ่งรายการต่อเขต Regional record จะถูกเขียนพร้อม source asset ID และ processor version หลังผ่านการตรวจสอบ จากนั้นจึงคำนวณ Heat Exposure Priority และส่ง affected-layer event
- Dataset version ที่ publish แล้วจะมี `RasterAssetMetadata` แบบ virtual URI (`earthengine://...`) พร้อม band/recipe/checksum/footprint โดยไม่คัดลอก raster ขนาดใหญ่ลง relational database
- ชั้น `temperature`, `ndvi`, `vegetation` และ `built` ใช้ provider-backed XYZ จาก source asset version เดียวกับที่ผ่าน validation เท่านั้น URL มี token อายุจำกัดจึง cache แบบ private และไม่ถูกเก็บถาวร หาก credential หรือ raster metadata ไม่พร้อม API จะตอบ `available=false` และ UI ใช้ district aggregate เป็น fallback โดยไม่สร้างภาพจำลอง

พื้นที่ที่มี valid coverage ต่ำกว่า 5% จะไม่ถูกเผยแพร่จาก observation นั้น; ช่วง 5–25% จะติดสถานะ `limited_coverage` แทนการปกปิดข้อจำกัดของเมฆ/ข้อมูลขาดหาย

### Population

- reference year and unit (`persons/pixel`, density, count) preserved
- raster sum/aggregation method respects source grid and avoids double counting
- no interpolation is described as a current observation

WorldPop connector ใช้ Global2 `/catalog` เพื่อตรวจ release/ปีที่มีอยู่ และอ่านข้อจำกัดจาก `/health` ก่อนแบ่ง `/batch` ตามจำนวน vertices, parts, features และขนาด JSON ส่งคำขอตามลำดับและคง geometry ต้นฉบับทุกจุด ไม่มีการ simplify ขอบเขตเพื่อให้ผ่าน quota หากเขตเดียวเกินข้อจำกัดจะหยุดพร้อมเหตุผล ต้องได้รับแต่ละ area ID ครบหนึ่งครั้งและปีตรงกันทุก batch ก่อนผ่าน validation และเผยแพร่ทั้ง version หาก batch ใดล้มเหลว แผนที่คง version ก่อนหน้า ดู [WorldPop v3 documentation](https://api.worldpop.org/v3/documentation) และ [OpenAPI](https://api.worldpop.org/v3/openapi.json)

ค่าเริ่มต้นเลือกปีล่าสุดที่ไม่เกินปีปัจจุบัน สามารถตรึงปีด้วย `WORLDPOP_REFERENCE_YEAR` ได้ ทุกผลลัพธ์ยังเป็น `model_estimate` รวมถึงปีปัจจุบัน ไม่ใช่การสำรวจคนสด เก็บ reference year/release/resolution/citation และไม่สร้าง uncertainty ที่ provider ไม่ได้ส่งมา ค่า rank/share ที่ provider คืนเป็นข้อมูลภายในแต่ละ batch จึงใช้ชื่อ metadata `provider_batch_rank` และ `provider_batch_share_of_total` เพื่อไม่ให้เข้าใจว่าเป็นอันดับรวมกรุงเทพฯ

WorldPop batch ที่ผ่านการตรวจรูปแบบเก็บ checkpoint ส่วนตัวได้ 24 ชั่วโมง เมื่อ retry จะไม่คำนวณ batch เดิมซ้ำหาก asset, geometry, year, provider และ processor ตรงกัน Checkpoint มี SHA-256/วันหมดอายุ ไม่เป็น map layer และไม่เปิดเผยผ่าน API ข้อมูลที่เผยแพร่ เมื่อ monitor สำเร็จจึงล้าง staging นี้โดยคงประวัติ dataset/validation ทั้งหมด

Anonymous API มี quota ต่ำกว่า API key ระบบบันทึก `X-RateLimit-*` ลง source check และ Celery เคารพ `Retry-After` เมื่อได้รับ HTTP 429 ห้ามใส่ key ลง repository หรือ frontend environment

### Vector candidates

OpenStreetMap snapshot จะไม่เผยแพร่เมื่อ Overpass tile ใดตอบ error หรือมี `remark` ระบุผล query ที่ไม่สมบูรณ์ แม้ HTTP status จะเป็น 200 ก็ตาม งานที่ล้มเหลวยังคง snapshot ก่อนหน้าและเก็บเหตุผลใน source check; ไม่ใช้ผลบางส่วนเพื่อลบ candidate ที่เคยผ่านการเผยแพร่

OSM tiles ที่สำเร็จเก็บในตาราง staging ส่วนตัว `source_processing_checkpoints` อายุ 30 นาที งาน retry ใช้ซ้ำได้เฉพาะ replication sequence, bbox, grid, provider และ processor เดิม หาก provider มี `timestamp_osm_base` ครบทุก tile ระบบบันทึกช่วงเวลาของ snapshot และใช้ต้นช่วงเป็นวันที่ต้นทาง; replication generation time ยังถูกเก็บแยกต่างหาก หากไม่มีเวลา snapshot จะระบุ `observation_time_kind=replication_checkpoint`

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

`NEXT_PUBLIC_MAP_STYLE_URL` selects the MapLibre style at image build time; its sources supply attribution. The default is the bundled `/styles/community-osm.json`, using real community OpenStreetMap raster tiles with always-visible attribution. It needs no basemap account or key, but is best effort with limited shared capacity. Production configuration also accepts an operator-approved HTTPS provider style. Confirm provider terms, attribution and traffic limits before opening public access. Do not use community tiles for bulk downloads or prefetching. Keep browser referrers and normal HTTP caching enabled as required by the [OSM tile policy](https://operations.osmfoundation.org/policies/tiles/). See [basemap delivery](basemap-delivery.md).
