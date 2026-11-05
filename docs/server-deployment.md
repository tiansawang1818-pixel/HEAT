# เตรียมเซิร์ฟเวอร์ HEAT//SHIFT

ชุดนี้เตรียมสำหรับ Linux server ที่มี Docker Engine และ Docker Compose 2.24.4 ขึ้นไป ยังไม่ได้เลือกหรือเปิดเซิร์ฟเวอร์จริง Caddy รับ HTTP/HTTPS ที่พอร์ต 80/443 และออก/ต่ออายุใบรับรองอัตโนมัติเมื่อโดเมนชี้มาที่เซิร์ฟเวอร์ ระบบฐานข้อมูล Redis API และเว็บไม่มีพอร์ตสาธารณะของตนเอง

## ตั้งค่าก่อนเริ่ม

1. คัดลอก `.env.production.example` เป็น `.env.production` แล้วจำกัดสิทธิ์ไฟล์ด้วย `chmod 600 .env.production`
2. ตั้งโดเมน, อีเมลต่ออายุใบรับรอง, รหัส PostgreSQL แบบสุ่ม และ `DATABASE_URL` ที่ใช้รหัสเดียวกัน แนะนำ `openssl rand -hex 32` เพื่อเลี่ยงอักขระที่ต้อง encode ใน URL
3. ลงทะเบียน SSO client พร้อม redirect URI `https://<domain>/api/auth/callback` กำหนด issuer, audience, JWKS และ role claim ให้ตรงกับ token ขององค์กร `AUTH_SESSION_SECRET` ต้องเป็นค่าสุ่มคนละค่ากับรหัสฐานข้อมูล
4. วาง Earth Engine service-account JSON นอก Git และกำหนด `GEE_CREDENTIALS_FILE` เป็น host path ของไฟล์จริง Compose mount เป็น secret ให้เฉพาะ API, migration และ workers ตรวจ project/สิทธิ์/quota ด้วย source check จริง ไม่ใช้บัญชีตัวอย่าง
5. กำหนด `NEXT_PUBLIC_MAP_STYLE_URL` จากผู้ให้บริการที่อนุญาตการใช้งานและปริมาณ traffic ของโครงการ ค่า frontend เหล่านี้ถูกฝังตอน build จึงต้อง rebuild เมื่อเปลี่ยน
6. ตรวจ subnet ของ Docker ไม่ชนกับเครือข่ายเซิร์ฟเวอร์ หากเปลี่ยนให้แก้ `HEATSHIFT_PROXY_SUBNET` และ `HEATSHIFT_PROXY_IP` คู่กัน Uvicorn เชื่อ forwarded client IP เฉพาะ Caddy address นี้ ห้ามเปิด API port หรือเพิ่ม wildcard trust

ค่า `.env.production` ใช้ผ่าน `--env-file` ไม่ถูกยกทั้งไฟล์เข้าสู่ container โหมด production บังคับปิดข้อมูลสาธิต/fixture เปิด rate limit และใช้ OIDC ฐานข้อมูลและ Redis อยู่บน Docker network ภายใน Redis ใช้ durable AOF และแยก session ใน logical DB 2 ต้องจำกัดสิทธิ์เข้าถึง host/Docker ตามนโยบายองค์กร

## ตรวจและเริ่มระบบ

```bash
make production-check
make production-up
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml ps
API_URL=https://<domain> WEB_URL=https://<domain> python3 scripts/smoke_test.py
python3 scripts/check_readiness.py --url https://<domain>
```

`production-check` ตรวจ rendered configuration โดยไม่พิมพ์ secret และปฏิเสธค่าตัวอย่างที่สำคัญ การผ่านขั้นตอนนี้ไม่ได้รับรอง credentials หรือความพร้อมของข้อมูลจริง ขั้นตอนเริ่มรัน migration ก่อน API/worker และเปิด Beat เพียง instance เดียว หลีกเลี่ยงการ scale Beat ซึ่งจะทำให้มี scheduler ซ้ำ

ล็อกอินที่ Data Observatory แล้วตรวจงานหนึ่งแหล่งข้อมูลต่อครั้ง เก็บ job ID ตรวจ source observation, validation report และ layer publication รวมทั้งดูว่าแผนที่อัปเดตโดยไม่ reload เมื่อข้อมูลไม่ครบ ระบบต้องแสดง unavailable/ข้อมูลไม่เพียงพอ ห้ามเปิด fixture เพื่อเติมภาพให้ดูพร้อม

MinIO เก่าใน Compose สำหรับ local ถูกแยกไป profile `local-storage` และไม่เริ่มใน production ปัจจุบัน raster provenance อ้าง immutable Earth Engine asset และ tile descriptor ที่มีอายุ หากเพิ่ม raster export ในอนาคต ต้องต่อ object storage ที่อนุมัติพร้อม bucket versioning/backup ก่อนเปิด processor นั้น ไม่มีการอ้างว่าไฟล์ดาวเทียมถูกสำรองใน S3 แล้ว

## สำรอง กู้คืน และอัปเดต

```bash
HEATSHIFT_COMPOSE_ENV=production make backup
```

เก็บ dump และ checksum แบบเข้ารหัสนอกเครื่องเซิร์ฟเวอร์ สคริปต์สำรองไม่ลบไฟล์เก่าอัตโนมัติ CI ซ้อม dump/restore ในฐานข้อมูลชั่วคราวแยก และเปรียบเทียบจำนวนแถวกับ checksum ทุกตาราง รวม geometry และ published versions การผ่าน CI ไม่แทนการซ้อมกู้คืนจากปลายทางสำรองจริงขององค์กร ดู [operations runbook](operations-runbook.md)

ก่อนอัปเดตให้สำรองข้อมูล บันทึก Git commit และ image digest ของรุ่นที่ใช้งาน จากนั้น build รุ่นใหม่และรัน migration โดยไม่ใช้ `down --volumes` ในเซิร์ฟเวอร์จริง หาก migration เปลี่ยน schema แบบย้อนกลับไม่ได้ ให้ restore ใน environment แยกและตรวจสอบก่อนสลับ traffic ห้ามสั่ง downgrade หรือเขียนทับฐานข้อมูลโดยไม่มีแผนกู้คืนที่ตรวจแล้ว

## ขอบเขตการตรวจ

CI ตรวจ production Compose isolation, build images, เปิดหน้าเว็บทั้ง 7 หน้า, API rewrite ภายใน Next, SSO gate และ SSE ผ่าน Caddy โดยใช้ HTTP เฉพาะเครือข่ายทดสอบและไม่มี workers ที่เรียก provider ส่วน HTTPS certificate, DNS, SSO จริง, ข้อมูล Earth Engine จริง, backup storage, alert delivery และความจุภายใต้ traffic จริง ต้องตรวจในเซิร์ฟเวอร์ที่เลือกก่อนเปิดใช้

การตั้งค่าอ้างอิง [Compose production](https://docs.docker.com/compose/how-tos/production/), [Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/), [Compose override/reset](https://docs.docker.com/reference/compose-file/merge/) และ [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https)
