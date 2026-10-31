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

## Required environment

```dotenv
GEE_PROJECT=your-project
GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/gee-service-account.json
GEE_MAX_CONCURRENT_EXPORTS=2
OPEN_METEO_BASE_URL=https://api.open-meteo.com/v1/forecast
OVERPASS_API_URL=https://overpass-api.de/api/interpreter
WORLDPOP_BASE_URL=https://data.worldpop.org
OVERTURE_RELEASE=
```

Mount Google service-account JSON as a Docker/Kubernetes secret; do not copy it into the image or repository. Production should prefer workload identity where supported.

## Validation gates

### Satellite-derived layers

- asset footprint intersects configured Bangkok boundary
- CRS/affine transform and pixel size are declared and sane
- required bands and QA bands exist
- scale factor/offset match the exact collection version
- cloud/shadow/snow/saturation mask applied
- valid coverage exceeds configured threshold; partial coverage remains explicit
- computed value distribution falls within scientifically plausible configured bounds
- output checksum, processor version and source asset ID recorded

### Population

- reference year and unit (`persons/pixel`, density, count) preserved
- raster sum/aggregation method respects source grid and avoids double counting
- no interpolation is described as a current observation

### Vector candidates

- geometry validity, CRS, duplicates and feature IDs checked
- suitability recalculated only where changed features intersect candidates
- provider attribution/license fields retained

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
