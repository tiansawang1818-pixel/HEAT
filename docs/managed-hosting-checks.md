# ตรวจ managed hosting ก่อนย้าย HEAT//SHIFT

ใช้ `scripts/verify_hosting_backends.py` ตรวจ provider ที่ได้รับอนุญาตแล้ว ไม่ provision, resize, restart ฐานข้อมูล เปิด extension หรือรัน server/worker/scheduler ในเครื่อง สคริปต์นี้ตรวจ backend compatibility เท่านั้น แม้ได้ exit code 0 ก็ไม่ใช่หลักฐานว่า CPU/RAM/storage ของแอปทั้งชุดพอหรือค่าใช้จ่ายเป็นศูนย์

## Credentials และ TLS

รับ `HOSTING_POSTGRES_URL` / `HOSTING_REDIS_URL` จาก secret injection หรือไฟล์ JSON ส่วนตัว mode `0600` ซึ่งมี fields `postgres_url` และ `redis_url` ใช้ URI จาก provider จริง ไม่ใส่ credentials ใน command arguments, แชต, Git, logs หรือ reports อย่าใช้ `.env.production` โดยไม่ตั้งใจ เพราะ probe ไม่โหลดค่าของแอปโดยอัตโนมัติ

PostgreSQL บังคับ `sslmode=verify-full` พร้อม public CA roots จาก certifi และ transaction แบบ read-only ถ้า PostGIS ไม่พร้อมใช้ จะไม่สร้าง extension ให้เอง Redis บังคับ `rediss://`, SNI และตรวจ certificate/hostname ไม่รับ query overrides ที่อาจลดการตรวจ TLS รายงาน exception เป็น fixed reason labels จึงไม่เผย URI/รหัสผ่านจากข้อความ provider

```sh
python scripts/verify_hosting_backends.py \
  --credentials-file /run/secrets/hosting-probe.json \
  --output /tmp/hosting-backends.json
```

Exit code `2` หมายถึง missing/error/blocked หรือหลักฐานยังไม่ครบ ห้ามเปลี่ยนเป็นผ่านเพราะ provider แสดง Healthy, PING ได้, มี PostgreSQL เปล่า หรือเปิด AOF อยู่

## คิวทดสอบหลัง restart

ทำเฉพาะ database ทดสอบที่ผู้รับผิดชอบอนุญาตให้ restart แล้ว โหมดแรกเขียน marker และ list สองข้อความใน namespace สุ่ม `heatshift:hosting-probe:<UUID>` มี TTL หนึ่งชั่วโมง ไม่ส่ง Celery tasks และไม่ใช้คิวงานจริง บันทึก marker file แบบ `0600` โดยไม่เขียนทับไฟล์เดิม

```sh
python scripts/verify_hosting_backends.py \
  --credentials-file /run/secrets/hosting-probe.json \
  --prepare-restart /tmp/hosting-restart-marker.json \
  --output /tmp/hosting-before-restart.json
```

จากนั้นผู้มีสิทธิ์ restart เฉพาะ test Redis ผ่าน dashboard/provider interface แล้วรัน:

```sh
python scripts/verify_hosting_backends.py \
  --credentials-file /run/secrets/hosting-probe.json \
  --verify-restart /tmp/hosting-restart-marker.json \
  --output /tmp/hosting-after-restart.json
```

ต้องพบ server `run_id` เปลี่ยนและ marker/ลำดับข้อความตรงเดิมจึงนับเป็น retained queue หากแค่ reconnect แล้วเป็น process เดิม จะเป็น pending ไม่ใช้แทน crash recovery หรือรับรอง RPO รายงานยังคง blocked ถ้ายืนยัน `appendfsync`/`noeviction` ไม่ได้ ต้องมี load, backpressure, TLS ของ clients จริง และ production recovery checks เพิ่มเติม

หลังจบลบเฉพาะไฟล์ credentials/marker ชั่วคราวที่สร้างไว้ตามนโยบาย secret handling; remote probe keys หมดอายุตาม TTL โดยไม่ลบ application keys ฐานข้อมูลทดสอบที่ยังคงอยู่ใช้ quota อยู่ แม้ไม่มีเว็บ production ห้ามเพิ่มหลาย project/บัญชีหรือเปิด paid capacity เพื่อหลบ free pool

## PostGIS แบบ rollback-only ในฐานข้อมูลใหม่

หลังพื้นที่คืนจริงและตรวจ free entitlement ของทั้งบัญชีแล้ว ใช้ flag ด้านล่างได้เฉพาะ URI ที่ระบุ hostname และชื่อ database `heat-pg16-check` ไม่มี query parameters ที่เปลี่ยนปลายทาง/ชื่อฐานข้อมูล สคริปต์บังคับ TLS แบบตรวจ hostname และตรวจชื่อฐานข้อมูลที่เชื่อมได้ซ้ำ ค่าเริ่มต้นที่ไม่มี flag ยังคงอ่านอย่างเดียว

```sh
python scripts/verify_hosting_backends.py \
  --credentials-file /run/secrets/hosting-probe.json \
  --postgis-transaction-test \
  --output /tmp/hosting-postgis-check.json
```

ชุดตรวจปฏิเสธ database ที่มี public tables อยู่แล้ว ยกเว้น `spatial_ref_sys` ของ PostGIS หาก provider ไม่มี extension package จะแสดง blocked และไม่พยายามติดตั้ง software ลงเครื่อง provider การเปิด PostGIS ชั่วคราว ตาราง geometry 100 ช่อง และ GiST index อยู่ภายใน transaction ที่บังคับ rollback เสมอ ตรวจ CRS 4326 → 3857 → 4326, validity, GeoJSON และแผน spatial query แล้วตรวจว่า extension state/public tables กลับเท่าเดิมและ temp table หาย ไม่มีการเพิ่ม observation, migrations หรือ pipeline publication

มี statement timeout 15 วินาทีและ lock timeout 2 วินาที ทดสอบทั้งเส้นทางสำเร็จ การรันซ้ำ และการล้มเหลวหลังเขียน temp rows ใน isolated CI บน PostGIS จริง การผ่านทดสอบนี้พิสูจน์ extension/SQL capabilities ในฐานข้อมูลที่ทดสอบเท่านั้น ไม่ใช่ load test, permanent PostGIS setup หรือ production/free-hosting approval

การผ่านวัน recovery ไม่ได้แปลว่าพื้นที่ถูกคืนแล้ว ต้องตรวจรายการ Recently deleted และยอด active + retained storage จาก provider ก่อนสร้าง database ใหม่ ห้ามใช้วันที่เพียงอย่างเดียวเพื่อข้าม gate

## หลักฐานรอบ Hostless

[รายงาน backend วันที่ 7 พฤศจิกายน 2026](verification/hostless-backends-2026-11-07.json) มีเฉพาะผลตรวจที่ตัดข้อมูลลับออก การกู้ PostgreSQL ไม่สำเร็จจึงยังไม่ผ่าน PostGIS หรือ whole-stack capacity ดู [รายละเอียดข้อจำกัด](no-card-hosting-research.md#ผลทดสอบ-backend-จริง)

การตรวจเพิ่มเติมแยก PostgreSQL SSLRequest กับ direct TLS โดยส่ง SNI/ALPN `postgresql` และบังคับ certificate/hostname verification ทั้งสองกรณี พบ response `S` ต่อ SSLRequest แต่ handshake ถูกตัดก่อน authentication ทั้งสองโหมด จึงไม่ส่ง password ไปทดลองลดความปลอดภัย ดู [รายงาน TLS](verification/hostless-postgres-tls-diagnostic.json) และ [วิธีวัดทรัพยากรใน CI](capacity-validation.md)

Celery กำหนด `broker_use_ssl` และ `redis_backend_use_ssl` แยกกัน พร้อมปฏิเสธ URI ที่ลด certificate/hostname checks ฝั่งเว็บใช้ node-redis พร้อม SNI ตาม hostname ของ `rediss://` ส่วน private Compose `redis://redis` ยังใช้ transport เดิม อ้างอิง [Celery SSL configuration](https://docs.celeryq.dev/en/stable/userguide/configuration.html#broker-use-ssl) และ [Hostless connection requirements](https://docs.hostless.cloud/databases/connecting)
