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

## หลักฐานรอบ Hostless

[รายงาน backend วันที่ 7 พฤศจิกายน 2026](verification/hostless-backends-2026-11-07.json) มีเฉพาะผลตรวจที่ตัดข้อมูลลับออก การกู้ PostgreSQL ไม่สำเร็จจึงยังไม่ผ่าน PostGIS หรือ whole-stack capacity ดู [รายละเอียดข้อจำกัด](no-card-hosting-research.md#ผลทดสอบ-backend-จริง)

Celery กำหนด `broker_use_ssl` และ `redis_backend_use_ssl` แยกกัน พร้อมปฏิเสธ URI ที่ลด certificate/hostname checks ฝั่งเว็บใช้ node-redis พร้อม SNI ตาม hostname ของ `rediss://` ส่วน private Compose `redis://redis` ยังใช้ transport เดิม อ้างอิง [Celery SSL configuration](https://docs.celeryq.dev/en/stable/userguide/configuration.html#broker-use-ssl) และ [Hostless connection requirements](https://docs.hostless.cloud/databases/connecting)
