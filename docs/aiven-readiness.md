# ผลตรวจ Aiven Free สำหรับ HEAT//SHIFT

ตรวจจริงวันที่ 8 พฤศจิกายน 2026 (Asia/Bangkok) เวลาของ SQL checks ใน [รายงานที่ตัดข้อมูลลับออก](verification/aiven-postgis-2026-11-08.json) เป็น UTC ไม่มี URI, รหัสผ่าน, account IDs, email verification links หรือ project CA ใน Git

## สิ่งที่ทำแล้ว

- สมัครด้วย GitHub หลังผู้ใช้ยืนยันข้อตกลงและสิทธิ์ที่ GitHub แสดง จากนั้นยืนยันอีเมลสำเร็จ ไม่มี repository integration หรือสิทธิ์องค์กรเพิ่มเติม
- สร้าง service `heat-postgis-check` และตรวจหน้า Service settings จริงว่าเป็น **Free-1-1gb**, Running, 1 CPU / RAM 1 GB / disk 1 GB, additional storage: None added, connection limit 20
- แผนฟรีให้ PostgreSQL **18.6** และไม่เปิดให้เลือกรุ่น จึงใช้รุ่นจริงที่ได้รับ ไม่อัปเกรดเป็นแผนเสียเงินเพื่อบังคับ PostgreSQL 16 บัญชีมีแบนเนอร์ trial credits ที่ผู้ให้บริการออกให้อัตโนมัติ แต่ไม่ได้เลือก Developer/Professional หรือใช้เครดิตกับ service นี้ และไม่ได้ผูกบัตร
- ตรวจ SQL ด้วย `sslmode=verify-full`, project CA และ hostname verification ผ่าน TLS 1.3 แม้ URI ตั้งต้นของ dashboard จะระบุ `sslmode=require`
- ตรวจ `SHOW max_connections` ได้ 20 ตรงกับ UI และสร้าง logical databases ทดสอบภายใน service เดียวได้ ไม่เพิ่ม service เพื่อรับโควตาเพิ่ม
- เปิด PostGIS **3.6.4** เฉพาะฐานข้อมูลทดสอบที่ตั้งใจไว้ แล้วตรวจ geometry 100 ช่อง, EPSG:4326 → 3857 → 4326, validity, GeoJSON และ GiST query plan ผ่าน รันซ้ำหลังเปิด extension แล้วผ่าน โดย geometry/index ถูก rollback ครบ ไม่มี environmental observation หรือ map publication
- สร้าง logical database `heatshift-readiness-check` แยกสำหรับ migrations: ถึง revision `20261106_0004`, สร้างครบ 21 application tables ตาม ORM และตรวจทุกตารางว่าง ไม่เริ่ม app server, worker, scheduler หรือ provider monitors

## การเปลี่ยนโค้ดเพื่อรองรับปลายทางนี้

`scripts/verify_hosting_backends.py` รับ `--postgres-ca-file` สำหรับ project CA ตรวจ PEM ก่อนเชื่อมและไม่ fallback ลด TLS เมื่อไฟล์ผิด ชุด spatial probe ยังต้องเปิด flag เองและยอมรับเพียง fresh `heat-pg16-check` หรือ `heat-postgis-check` ไม่เขียน DDL ใน `defaultdb` หรือฐานข้อมูลแอปที่มีตารางอยู่

API รองรับ pool size/overflow/timeout ต่อ process; production template ใช้ 2 / 0 / 10 วินาที ต้องรวม replicas และ Celery children จริงไม่ให้เกินเพดาน 20 พร้อมเหลือช่อง admin/backup แก้ SSE ให้คืน transaction/connection ก่อนส่ง frames และก่อนรอ เพิ่มการทดสอบ publisher ที่ใช้ pool เพียงหนึ่ง connection ขณะมี client แผนที่เปิดอยู่

[CI run 42403035779](https://github.com/tiansawang1818-pixel/HEAT/actions/runs/42403035779) ของ source `77dd718553156e6e954e52082a3f1a7d1a0a14fc` ผ่านครบ 5 jobs: API 215 tests และ dependency audit, Web 38 tests/TypeScript/build, integration 7 tests บน amd64 และ ARM จริง, release containers รวม HTTPS/SSE/admin gates/backup recovery/full-runtime baseline ผ่านทั้งหมด

## สิ่งที่ยังไม่ผ่าน

นี่พิสูจน์ **PostgreSQL/PostGIS และ migrations บน Aiven Free** ไม่ใช่การเปิด HEAT ทั้งระบบแล้ว ยังต้อง:

1. ตรวจ resource admission และโหลดของ runtime ทั้งชุดบน Hostless บัญชีเดิมภายใน pool จริง การย้าย PostgreSQL ไม่คืน retained storage ของ Hostless
2. ตรวจ queue backpressure, abrupt-crash recovery และเงื่อนไข Redis persistence ที่ยังอ่าน CONFIG ไม่ได้ ไม่ย้าย broker ไป Aiven Valkey โดยถือว่า snapshot เท่ากับ durable queue
3. ทดสอบ off-host backup/restore จาก service จริง และเตรียมการเปลี่ยน project CA เมื่อ provider rotate ใบรับรอง ผล backup tests ใน CI ไม่แทน provider recovery
4. จัด secret injection, SSO ของผู้ดูแล, สิทธิ์ Earth Engine และข้อมูลสิ่งแวดล้อม/หลักฐานต้นทุนที่ผ่าน validation ก่อน release audit

[Aiven Free](https://aiven.io/docs/products/postgresql/concepts/pg-free-tier) เป็น single node ไม่มี SLA และอาจปิด service เมื่อไม่มีการใช้งานตามเงื่อนไข ไม่ใช้ traffic ปลอมเพื่อเลี่ยงนโยบาย ไม่มี production monitors รันอยู่ในขณะนี้ จึงไม่อ้างว่าการเก็บข้อมูลเริ่มทำงานต่อเนื่องแล้ว ไม่มีข้อความส่งถึง support แทนผู้ใช้ และไม่ได้แตะ PostgreSQL ของ Hostless ที่รอลบ

ตัว probe รวม PostgreSQL/Redis จะยังแสดง aggregate `blocked` หากไม่ได้ระบุ Redis ในรอบนี้ แม้ PostgreSQL และ spatial subchecks เป็น `passed` รายงานแยกขอบเขตนี้ไว้ชัดเจน
