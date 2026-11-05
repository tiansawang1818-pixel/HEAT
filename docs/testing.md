# การทดสอบและหลักฐาน CI

ทุก push จะรัน GitHub Actions แยกเป็น 4 งาน:

- `api`: unit/API tests ด้วย SQLite รวม validation, retry, interrupted-worker recovery, locks, optimizer และ freshness
- `web`: client API/OIDC flow tests (จริงสำหรับ code/PKCE/nonce verification โดย mock provider และ Redis), dependency audit, TypeScript และ Next.js production build
- `stack`: PostgreSQL/PostGIS และ Redis จริง พร้อม migration, API process และ Celery worker process แยกกัน
- `containers`: production Compose isolation, Caddy validation, release image builds และ HTTP smoke ผ่าน Caddy โดยไม่เริ่ม source monitors หรือเรียก provider จริง

งาน `api` ตรวจ dependency audit ด้วย งาน `stack` ซ้อม pg_dump/pg_restore ไปยัง `heatshift_restore_ci` ที่สร้างใหม่ เปรียบเทียบ row count/checksum ทุก application table และตรวจ geometry/published version อีกครั้ง สคริปต์ปฏิเสธการทับฐานข้อมูลเดิมและรันเฉพาะ isolated CI container

งาน `stack` ตรวจ GiST index, geometry SRID, Redis lock ownership/expiry, authorized job dispatch, duplicate skipping, viewport `ST_Intersects`, ETag invalidation และ SSE stream เดิมที่รับทั้ง publication แรกและ publication ถัดไป API map เปลี่ยนค่าตามเวอร์ชันที่ publish โดย stream ไม่ถูกเปิดใหม่

fixtures ในงานนี้ระบุ `synthetic_demo` และไม่มีการเรียก Earth Engine หรือผู้ให้บริการภายนอก การผ่าน CI จึงยืนยันการเชื่อมต่อส่วนประกอบของระบบ ไม่ได้ยืนยัน provider credentials, ความถูกต้องของข้อมูลจริง หรือผล cooling ของ intervention

รัน unit/API tests ในเครื่อง:

```bash
cd apps/api
.venv/bin/pytest tests
```

การรัน `pytest integration -v` ต้องเตรียม PostGIS/Redis แยกสำหรับ CI, รัน `alembic upgrade head` และตั้ง `HEATSHIFT_RUN_STACK_TESTS=true` โดยใช้ database ชื่อ `heatshift_ci` บน localhost เท่านั้น test จะปฏิเสธชื่อฐานข้อมูลอื่นและไม่ล้างฐานข้อมูลให้อัตโนมัติ สำหรับการทดสอบซ้ำให้สร้าง stack ชั่วคราวใหม่ ใช้ credentials สำหรับ CI เท่านั้น

ก่อนเปิดใช้งานจริง ให้รัน source check ที่ได้รับอนุญาตอย่างน้อยหนึ่งครั้งต่อแหล่งข้อมูล แล้วตรวจ observation dates, validation reports และ raster/map alignment จากข้อมูลจริงใน Data Observatory เพิ่มเติมจาก CI
