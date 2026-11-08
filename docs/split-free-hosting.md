# ทางเลือกแยก hosting เพื่อไม่รอ PostgreSQL ของ Hostless

> อัปเดต 8 พฤศจิกายน 2026: มีเว็บนำร่องบน Hostless และ Open-Meteo publication จริงแล้ว แต่ release ใหม่/readiness และข้อมูลดาวเทียมยังไม่ครบ ดู [สถานะปัจจุบัน](predeployment-status.md) รายละเอียดด้านล่างเป็นผลการตรวจช่วงเตรียมฐานข้อมูลก่อนเปิดแอป


ตรวจเอกสารทางการวันที่ 8 พฤศจิกายน 2026 (Asia/Bangkok) ต่อมาผู้ใช้ยืนยันการสมัครและสิทธิ์ GitHub ตามหน้าที่แสดง จึงสมัคร/ยืนยันอีเมล Aiven และสร้าง PostgreSQL service `heat-postgis-check` บนแผน **Free-1-1gb** แล้ว หน้า service เปลี่ยนเป็น Running ให้ PostgreSQL 18 (Free เลือกรุ่นเองไม่ได้), 1 CPU / RAM 1 GB / disk 1 GB, connection limit 20 ยังไม่ได้เปลี่ยนปลายทางของ HEAT หรือยืนยันว่าทั้งระบบรันฟรีผ่านแล้ว

## ตัวเลือกแรก: Aiven PostgreSQL + Hostless บัญชีเดิม

[Aiven Free PostgreSQL](https://aiven.io/free-postgresql-database) ระบุว่าไม่ใช้บัตร ไม่มีวันหมดอายุ และรองรับ PostGIS โดยให้ 1 CPU / RAM 1 GB / disk 1 GB ต่อ service ส่วน [ข้อจำกัดแผนฟรี](https://aiven.io/docs/products/postgresql/concepts/pg-free-tier) ระบุเพดาน 20 connections ไม่มี connection pool และไม่มี SLA หากไม่มี initial/continuative usage ผู้ให้บริการมีสิทธิ์ปิด service หลังแจ้งเตือน จึงไม่รับรอง uptime หรือถือว่าเพียงสมัครแล้วผ่าน production

แนวทางนี้ใช้ provider คนละรายตามสิทธิ์ปกติ ไม่สร้างบัญชี Hostless ซ้ำ และไม่ต้องเพิ่ม PostgreSQL ลง storage pool ที่ค้างอยู่ จึงเริ่ม **ทดสอบ PostGIS บน Aiven** ได้โดยไม่รอวันที่ 15 พฤศจิกายน แต่การเปิดแอปครบชุดยังมีด่าน resource admission, load และสิทธิ์ข้อมูลจริง

| ส่วนระบบ | ปลายทางที่เสนอ | หลักฐานและสิ่งที่ต้องตรวจ |
|---|---|---|
| PostgreSQL/PostGIS | Aiven แผน Free หนึ่ง service | ตรวจ TLS, PostGIS 3.6.4, CRS/index/rollback และ migrations จริงผ่านแล้ว; ยังไม่มี load/backup restore จาก provider จริง |
| Redis broker / results / sessions | Hostless Redis เดิม | ผ่าน TLS, DB 0/1/2 และ graceful restart แล้ว; abrupt-crash recovery/appendfsync ยังไม่ผ่าน |
| Next.js, FastAPI, ingestion worker, dispatcher, Beat | Hostless บัญชีเดิม | [รองรับ Docker/apps/workers](https://docs.hostless.cloud/app/hostlessfile) แต่ต้องตรวจ entitlement และ quote รวมทุก process จริง ไม่อ้างว่า pool ที่เหลือพอจาก baseline CI |
| PostgreSQL ที่รอลบ | คง recovery state เดิม | ไม่ restore ไม่ force-delete และไม่นับ retained storage ว่าคืนแล้ว |

ตามขนาด Redis ที่เคยยืนยัน 0.2 vCPU / 256 MiB จะเหลือเชิงคำนวณ 0.3 vCPU / 768 MiB จาก pool 0.5 vCPU / 1024 MiB สำหรับ app processes หาก PostgreSQL ที่หยุดไม่ถูกนับ CPU/RAM แล้ว ต้องยืนยันยอดจริงใน dashboard ก่อนใช้ตัวเลขนี้ ปริมาณ storage เดิม 256 MiB กับ Redis 256 MiB ยังคงใช้ pool 512 MiB อยู่ การใช้ฐานข้อมูลภายนอกไม่ทำให้ storage ที่รอลบหายไป

ขั้นตรวจต่อที่เป็นรูปธรรม:

1. เปิดบัญชี Aiven ปกติเพียงบัญชีเดียว เลือก **Free** เท่านั้น ไม่เลือก Developer/Hobbyist หรือ credit trial และไม่ผูกบัตร ตรวจการยอมรับข้อตกลงก่อนสมัครจริง
2. ใช้ฐานข้อมูลทดสอบแยก ไม่มีข้อมูลสิ่งแวดล้อมหรือข้อมูลแอป production เก็บ credentials ผ่าน private secret files เท่านั้น
3. PostgreSQL ของ Aiven ใช้ [project CA certificate](https://aiven.io/docs/platform/concepts/tls-ssl-certificates) สำหรับ `sslmode=verify-full` เตรียม `--postgres-ca-file` ใน probe แล้ว โดย parse CA ก่อนเชื่อมและหยุดเมื่อไฟล์ผิด ห้ามแก้ปัญหาด้วยการปิด certificate/hostname verification ต้องใช้ CA ของ service จริงและทดสอบ TLS ก่อนอ้างว่าเชื่อมได้
4. ตรวจ `pg_available_extensions`, รุ่น PostgreSQL/PostGIS ที่ได้จริง และสิทธิ์เปิด extension แล้วค่อยทดสอบ geometry/index แบบ transaction บน fresh database ที่ระบุแน่ชัด ไม่เปลี่ยน default read-only probe ให้เขียนได้โดยปริยาย
5. กำหนด connection budget ให้ API, workers, Beat และงานตรวจรวมไม่เกิน 20 โดยเหลือช่องสำหรับ admin/backup เตรียม pool size/overflow/timeout ต่อ process และคืน connection ของ SSE ระหว่างรอแล้ว ยังต้องรวม replicas และ Celery children จริงก่อน deploy ไม่ถือว่าค่า template เป็น global cap
6. ตรวจ Hostless app/worker requests และจำนวน process ที่บัญชีอนุญาตจริงภายใน pool โดยไม่เปิด paid volume หากไม่ผ่านให้รายงาน blocker ไม่เพิ่มการคิดเงินเอง
7. ทดสอบ ingestion → validated publication → SSE, โหลดจริง, worker crash/redelivery และ off-host recovery ก่อนเปลี่ยนสถานะว่าพร้อมใช้งาน ไม่มี heartbeat/traffic ปลอมเพื่อเลี่ยง inactivity rules

## ตัวเลือกสำรองและเหตุผลที่ยังไม่เลือกก่อน

| ตัวเลือก | สิ่งที่มีจริงตามเอกสาร | ข้อจำกัดสำหรับ HEAT |
|---|---|---|
| [Supabase Free](https://supabase.com/pricing) + Hostless runtime | PostgreSQL 500 MB / RAM 500 MB และ [รองรับ PostGIS](https://supabase.com/docs/guides/database/extensions/postgis) | พักหลัง inactivity หนึ่งสัปดาห์; ต้องตรวจนิยาม activity/บัญชี/quotas และทดสอบ SQL compatibility ไม่ใช่เครื่องรัน Celery โดยตัวมันเอง |
| [Neon Free](https://neon.com/pricing) + Hostless runtime | ไม่ใช้บัตร, 1 GB ต่อ project ตาม pricing ปัจจุบัน, 100 CU-hours/เดือน; [มี PostGIS](https://neon.com/docs/extensions/postgis) | [scale to zero หลังว่าง 5 นาทีและ suspend เมื่อ CU-hours หมด](https://neon.com/docs/introduction/plans); dispatcher ทุก 60 วินาทีของโค้ดปัจจุบันอาจทำให้ไม่พัก จึงไม่ใช่ตัวแทนที่พร้อมใช้ทันที |
| [Aiven Free Valkey](https://aiven.io/docs/products/valkey/concepts/valkey-free-tier) | ไม่ใช้บัตร, RAM 1 GB, maxmemory 50%; หนึ่ง service ของชนิดนี้ต่อองค์กร | [เอกสาร persistence](https://aiven.io/docs/products/valkey/concepts/memory-usage) อธิบาย snapshot โดย default ทุก 10 นาที ยังไม่ได้พิสูจน์ AOF หรือ queue durability จึงไม่ย้ายคิวสำคัญจาก Redis เดิมเพียงเพราะ PING ได้ |
| [Deplexo Free](https://deplexo.com/free-hosting) | ระบุไม่ใช้บัตร/ไม่พัก, RAM 128 MB, หนึ่ง project | เล็กเกินการยกทั้ง HEAT stack ไปใช้โดยไม่มีการแยก process และวัดโหลดจริง ยังไม่ได้ตรวจบัญชี/สร้าง resource |
| [livemy.app Free](https://livemy.app/pricing) | บัญชีฟรีไม่ใช้บัตร | project ออนไลน์ได้เพียง 3 วัน; ข้อความหน้า Docker ว่า never sleeps ไม่ทดแทนข้อจำกัดอายุ project จึงไม่เลือกสำหรับงานต่อเนื่อง |

การคำนวณ Neon เป็นการประเมินจากเงื่อนไข: ที่ minimum 0.25 CU เปิด 24 ชั่วโมง × 30 วัน ใช้ 180 CU-hours มากกว่าเพดาน 100 ไม่ใช่ผลวัดจริง ต้องแก้การตื่นของ scheduler/connection และวัด usage ก่อนพิจารณาตัวนี้ เอกสาร plans บางส่วนยังระบุ storage 0.5 GB ต่างจาก pricing 1 GB จึงต้องยืนยัน entitlement ในบัญชีจริง ไม่เพิ่มหลาย project เพื่อกระจายงานหลบ quota

ฐานข้อมูลที่มี inactivity rule อาจใช้กับงานจริงเป็นระยะได้ การพัก compute ของ database ที่ resume จาก SQL เองต่างจากการพัก worker จนไม่มีผู้ตรวจข้อมูล อย่างไรก็ตามยังไม่มีผลทดสอบว่าบริการเหล่านี้รองรับ schedule/โหลดจริงของ HEAT ภายในโควตา และไม่ใช้ข้ออ้างนี้สร้าง traffic ปลอม

## สถานะการตัดสินใจ

ผู้ใช้เลือก Aiven และดำเนินการจริงแล้ว ดู [Aiven readiness](aiven-readiness.md) และ [รายงาน SQL/PostGIS/migrations](verification/aiven-postgis-2026-11-08.json) ไม่ต้องรอ retained storage ของ Hostless เพื่อผ่านด่าน PostgreSQL นี้

แนะนำตรวจ **Aiven PostgreSQL Free + Hostless บัญชีเดิม** ก่อน เพราะแก้จุดที่ติด PostgreSQL โดยไม่เพิ่ม storage ของ Hostless ไม่ใช้บัตร และไม่เปลี่ยนคิวที่เคยตรวจได้แล้ว มี fallback เป็น Supabase; Neon ต้องแก้/วัด compute budget เพิ่ม จึงยังไม่ใช่ตัวเลือกแรก

มี PostgreSQL Free ทดสอบบน Aiven แล้ว ไม่มี production deployment และไม่มีการติดต่อ support แทนผู้ใช้ บัญชีแสดง trial credits ที่ผู้ให้บริการให้มาอัตโนมัติ แต่ service เลือก Free จริง ไม่มีบริการ Developer/Professional หรือการผูกบัตร รายการนี้ไม่ยกเลิกด่าน Earth Engine/SSO/หลักฐานต้นทุนและพื้นที่จริงใน [predeployment status](predeployment-status.md)
