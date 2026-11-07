# การทดสอบและหลักฐาน CI

ทุก push ที่เปลี่ยนโค้ด/config และทุก pull request จะรัน GitHub Actions แยกเป็น 5 งาน ภายใต้โควตาบัญชีและเพดานค่าใช้จ่ายเดิม (push ที่เปลี่ยนเฉพาะคู่มือข้ามการทดสอบซ้ำ):

- `api`: unit/API tests ด้วย SQLite รวม validation, retry, interrupted-worker recovery, locks, optimizer และ freshness
- `web`: client API/OIDC flow tests (ตรวจ code/PKCE/nonce โดย mock provider), session encryption/TTL/logout/fail-closed กับ Redis จริง, dependency audit, TypeScript และ Next.js production build
- `stack`: PostgreSQL/PostGIS และ Redis จริง พร้อม migration, API process และ Celery worker process แยกกัน
- `containers`: production Compose isolation, Caddy validation, release image builds และ HTTP smoke ผ่าน Caddy โดยไม่เริ่ม source monitors หรือเรียก provider จริง
- `arm64`: สร้าง PostGIS/API/Caddy บน standard ARM runner จริง แล้วตรวจ migration, spatial queries, Redis locks, Celery/Beat และ publication ใน stack ชั่วคราว ไม่ใช่การเปิด cloud hosting

Historical ingestion tests ตรวจ page/cursor ของ Landsat ทั้งสอง collection, finite processing/catalogue budgets ที่รวม retry, duplicate delivery, worker recovery, cancellation, immutable resume และการคง observation/source check ล่าสุด Client subscription tests ตรวจ affected-layer refresh, metadata polling, stream outage, event deduplication และ cleanup โดยไม่เปลี่ยนเจ้าของ state ของมุมมอง/พื้นที่ที่เลือก

Optimizer tests เทียบ OR-Tools กับ exhaustive portfolios ขนาดเล็ก ตรวจเพดานงบจริง ต้นทุนเศษบาท การปิด intervention/candidate ทั้งหมด น้ำหนัก custom โดยไม่มีค่าแฝง และการคง snapshot เดิมเมื่อ candidate เปลี่ยน Scenario baselines จัดอันดับจากอุณหภูมิ/beneficiary evidence จริงและบอกข้อมูลไม่พอเมื่อหลักฐานขาด

SSE ต้องมี `Cache-Control: no-cache, no-transform` เพื่อไม่ให้ Next.js rewrite หรือ proxy บีบอัดแล้วค้างข้อความเล็ก งาน container ตรวจ initial frame ทั้งผ่าน Caddy และ Next โดยส่ง `Accept-Encoding: gzip, deflate` ไปด้วย และปฏิเสธการบีบอัด event stream

งาน container ใช้ HTTPS จริงกับ private CA ภายใน CI เพื่อตรวจ certificate/hostname, secure-cookie attributes และ security headers โดยไม่ใช้ insecure TLS และไม่เปลี่ยน trust store ของ host พร้อม read-only latency smoke 60 requests, concurrency 4 รายงาน release audit ต้องยังติด blocker เพราะ CI ไม่มีข้อมูล provider จริง รายงานทั้งสองดาวน์โหลดได้จาก artifact `release-verification-reports`

งาน `api` ตรวจ dependency audit ด้วย งาน `stack` ซ้อม pg_dump/pg_restore ไปยัง `heatshift_restore_ci` ที่สร้างใหม่ เปรียบเทียบ row count/checksum ทุก application table และตรวจ geometry/published version อีกครั้ง สคริปต์ปฏิเสธการทับฐานข้อมูลเดิมและรันเฉพาะ isolated CI container

งาน `stack` ตรวจ GiST index, geometry SRID, Redis lock ownership/expiry, authorized job dispatch, duplicate skipping, viewport `ST_Intersects`, ETag invalidation และ SSE stream เดิมที่รับทั้ง publication แรกและ publication ถัดไป API map เปลี่ยนค่าตามเวอร์ชันที่ publish โดย stream ไม่ถูกเปิดใหม่

fixtures ในงานนี้ระบุ `synthetic_demo` และไม่มีการเรียก Earth Engine หรือผู้ให้บริการภายนอก การผ่าน CI จึงยืนยันการเชื่อมต่อส่วนประกอบของระบบ ไม่ได้ยืนยัน provider credentials, ความถูกต้องของข้อมูลจริง หรือผล cooling ของ intervention

การทดสอบ scheduler ใช้ฐานข้อมูลใหม่ชื่อ `heatshift_scheduler_ci`, Redis DB 3 และ HTTP provider จำลองบน localhost แล้วเริ่ม Celery Beat/worker จริงโดยไม่เรียก manual-refresh API ตรวจการพบข้อมูลครั้งแรก การข้าม asset เดิม การเผยแพร่ revision ถัดไป และการคงข้อมูลเดิมเมื่อ provider ส่งข้อมูลไม่ผ่าน validation ค่าใน mock provider ใช้เฉพาะการทดสอบโครงสร้างระบบและไม่ใช่ข้อมูลอากาศจริง

CI ตรวจ Prometheus alert rules ด้วย `promtool`, เริ่ม Alertmanager จริง และส่ง alert สังเคราะห์ไปยัง receiver จำลองใน Docker network เพื่อยืนยัน notification delivery ส่วนเส้นทางปลายทางจริงขององค์กรและการกู้คืนจาก off-host backup ยังต้องซ้อมในสภาพแวดล้อมจริง

WorldPop unit tests ใช้ค่าประชากรสังเคราะห์แยกจากข้อมูลจริง ตรวจ batching ตามข้อจำกัด geometry เดิม, การปฏิเสธ area ID ที่ซ้ำ/หาย/สลับ, ค่าที่ไม่เป็นจำนวนจำกัด และ timeout หลัง batch แรก โดยยืนยันว่า version ก่อนหน้ายังใช้ได้และ retry ไม่แก้ไขประวัติที่เผยแพร่แล้ว การทดสอบกับ provider จริงต้องทำแยกตาม quota ไม่ได้รันทุกครั้งใน CI

รัน unit/API tests ในเครื่อง:

```bash
cd apps/api
.venv/bin/pytest tests
```

การรัน `pytest integration -v` ต้องเตรียม PostGIS/Redis แยกสำหรับ CI, รัน `alembic upgrade head` และตั้ง `HEATSHIFT_RUN_STACK_TESTS=true` โดยใช้ database ชื่อ `heatshift_ci` บน localhost เท่านั้น test จะปฏิเสธชื่อฐานข้อมูลอื่นและไม่ล้างฐานข้อมูลให้อัตโนมัติ สำหรับการทดสอบซ้ำให้สร้าง stack ชั่วคราวใหม่ ใช้ credentials สำหรับ CI เท่านั้น

ก่อนเปิดใช้งานจริง ให้รัน source check ที่ได้รับอนุญาตอย่างน้อยหนึ่งครั้งต่อแหล่งข้อมูล แล้วตรวจ observation dates, validation reports และ raster/map alignment จากข้อมูลจริงใน Data Observatory เพิ่มเติมจาก CI
