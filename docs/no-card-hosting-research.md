# ตรวจปลายทางฟรีที่ไม่ใช้บัตรเพิ่มเติม

วันที่ตรวจ: 7 พฤศจิกายน 2026 — ผู้ใช้ยืนยันการเชื่อม GitHub และสร้าง/เข้าบัญชี Hostless ฟรีแล้ว ตรวจหน้าบัญชีจริงสำเร็จ แต่ยังไม่ได้เปิด app, worker หรือ database เพิ่มบัตร หรือติดตั้ง GitHub App ให้เข้าถึง repository HEAT การพบข้อเสนอฟรีหรือเข้าสู่บัญชีได้ยังไม่เท่ากับได้รับทรัพยากรที่รันระบบครบได้จริง

## Hostless: ตัวเลือก PaaS ที่ยังต้องยืนยัน

หน้า [Apps](https://hostless.cloud/products/apps) และ [Databases](https://hostless.cloud/products/databases) ระบุว่าแอปและ worker ไม่พักเมื่อ idle พร้อม managed PostgreSQL/Redis และไม่ต้องใช้บัตรภายในโควตาฟรี จึงเป็นตัวเลือกสำหรับตรวจเพิ่มเติม ไม่ใช่คำรับรองว่า HEAT ทั้ง stack รันฟรีได้แล้ว

[Resource limits](https://docs.hostless.cloud/plans/limits) ระบุนโยบาย shared pool ที่กำลังทยอยใช้: CPU 0.5 vCPU, RAM 1 GiB และ database storage 512 MiB **รวมทั้งบัญชี** เอกสาร [free tier](https://docs.hostless.cloud/plans/free-tier) บางส่วนยังกล่าวถึงพื้นที่ 256 MiB หรือแผนแบบจำกัดจำนวน resource จึงไม่ใช้หน้านั้นแทน entitlement ของบัญชีจริง รอบที่ตรวจยืนยันตัวเลข 0.5 vCPU / 1024 MiB RAM / 512 MiB storage จากทั้งหน้า Current billing และหน้า Account billing ที่มีป้าย Preview ตรงกัน

[ค่าตั้งต้นฐานข้อมูล](https://docs.hostless.cloud/databases/create) คือ CPU 0.25 vCPU, RAM 512 MiB, storage 512 MiB ต่อ database หากเปิด PostgreSQL และ Redis ด้วย default ทั้งคู่ จะใช้ CPU/RAM shared pool ครบและเกิน storage pool ก่อนรวมเว็บ/worker จึงไม่สามารถสรุปว่า stack ปัจจุบันจะพอดีได้จากคำว่า free ต้องตรวจ minimum sizes และวัดการใช้จริงก่อน

รายการที่ยังไม่ยืนยัน:

- managed PostgreSQL ติดตั้ง PostGIS และให้สิทธิ์ `CREATE EXTENSION postgis` ได้หรือไม่ เอกสารที่อ่านยืนยัน PostgreSQL แต่ไม่ได้ยืนยัน PostGIS
- Redis persistence/queue recovery, connection limits และ session storage ที่เหมาะสมกับ Celery/SSO
- RAM/CPU ของ Next.js, API, worker, dispatcher และ Beat รวมกับฐานข้อมูล ภายใต้ resource requests ที่ provider อนุญาตจริง
- การยอมรับ resource configuration ของทั้ง stack ภายใน entitlement ที่ตรวจแล้ว และการป้องกันค่าใช้จ่ายส่วนเกินที่ตรงข้อกำหนดผู้ใช้

### ผลตรวจหน้าบัญชีจริง

ตรวจช่องทางเชื่อมแล้วไม่พบ Hostless plugin ในรายการที่ค้นได้ จึงใช้หน้าเว็บทางการ ผู้ใช้ยืนยันสิทธิ์ที่แสดงและการสร้าง/เข้าบัญชีฟรีภายใต้ [Terms of Service](https://hostless.cloud/terms) และ [Privacy Policy](https://hostless.cloud/privacy) แล้ว จึงกด Authorize และยืนยันอีเมลจนเข้าสู่ dashboard ได้สำเร็จ หน้า Connected accounts แสดง GitHub เป็น Healthy

การเชื่อมยืนยันตัวตนนี้แยกจากการติดตั้ง Hostless GitHub App ให้เข้าถึง source repository หน้าติดตั้งเปิดขึ้นหลัง sign-in แต่ยังไม่ได้ติดตั้งหรือเลือก repository ใด ๆ ไม่เก็บอีเมลส่วนตัว, account IDs, OAuth state, รหัสยืนยัน, credentials หรือภาพบัญชีใน Git

| รายการตรวจจาก UI จริง | ผล |
|---|---|
| บัญชีและ project | เข้าบัญชีได้ มี project เริ่มต้น ยังไม่มี runtime resources |
| รอบ billing ที่แสดง | 7 พฤศจิกายน – 7 ธันวาคม 2026 |
| Free entitlement | 0.5 vCPU / 1024 MiB RAM / 512 MiB storage รวมทุก project ของบัญชี; Current billing ระบุว่าแชร์ระหว่าง app, worker, cron, database และ Objects |
| การชำระเงิน | ไม่มีบัตร ไม่มีเครดิต ยอดใช้/ค้างจ่ายที่แสดง USD 0.00 ไม่ได้เปลี่ยน spending limit ซึ่งหน้า Preview แสดง No limit |
| Database engine | ตัวเลือก PostgreSQL, Valkey, Redis, ClickHouse, CouchDB, MongoDB; PostgreSQL ที่เลือกแสดง image `postgres:17` ไม่มีตัวเลือก PostGIS ในรายการที่เปิดตรวจ |
| ขนาด database แบบ Custom | CPU ต่ำสุด 0.2 vCPU, RAM ต่ำสุด 256 MiB, storage ต่ำสุด 256 MiB (`256Mi` ในหน้า Review) |
| Quote ก่อน deploy | PostgreSQL ขนาดต่ำสุดแสดง Est. monthly cost USD 0.00; **ไม่ได้กด Deploy** จึงยังไม่ยืนยัน provisioning, extension หรือ persistence |
| Marketplace | รายการ 13 บริการที่แสดงไม่มี PostGIS; ไม่ได้ deploy บริการใด |

หาก PostgreSQL และ Redis ใช้ขนาดต่ำสุดเดียวกัน จะรวมเป็น **0.4 vCPU / 512 MiB RAM / 512 MiB storage** เหลือเพียง 0.1 vCPU / 512 MiB RAM และไม่มีพื้นที่ database pool ที่เหลือ สำหรับ Next.js, API, ingestion worker, control worker และ Beat ใน architecture ปัจจุบัน นี่เป็นการคำนวณ resource requests จากตัวเลขใน UI ไม่ใช่ benchmark และไม่ใช่หลักฐานว่ารันงาน geospatial จริงได้ภายใน RAM/CPU ดังกล่าว

ไม่พบตัวเลือก PostGIS **ไม่เท่ากับพิสูจน์ว่า extension ใช้ไม่ได้** ต้องตรวจ `pg_available_extensions`, สิทธิ์เปิด extension และคำสั่ง spatial บนฐานข้อมูลจริงก่อนย้าย migrations ไปใช้ ไม่เปิด PostgreSQL เปล่าแล้วอ้างว่าได้ PostGIS แล้ว ยังไม่ได้ทดสอบ Redis persistence/recovery หรือขนาดการใช้งานจริง และ quote ราคาไม่ใช่ทรัพยากรที่ provision สำเร็จ

สำหรับการเชื่อมในอนาคต [connection documentation](https://docs.hostless.cloud/databases/connecting) ระบุให้ PostgreSQL ตรวจ certificate/hostname ด้วย `sslmode=verify-full` และ Redis/Valkey ใช้ TLS พร้อม SNI ของ hostname จริง ต้องทดสอบ client ของ HEAT โดยคงการตรวจ certificate ไว้ ไม่ใช้ `sslmode=require` เป็นหลักฐานว่า hostname ผ่านการตรวจแล้ว

[Spending limit](https://docs.hostless.cloud/plans/limits) ไม่ใช่ hard invoice cap: เอกสารระบุว่าเมื่อถึง limit จะบล็อก capacity แบบคิดเงินเพิ่ม แต่ resource ที่ทำงานอยู่ยังอาจคิดเงิน ดังนั้นห้ามเปิด paid capacity/ใส่บัตร/เติมเครดิตเพื่อให้ผ่าน admission โดยอัตโนมัติ

บริการ [Virtual Machines](https://hostless.cloud/products/virtual-machines) ของ Hostless เป็น early access แบบเสียเงิน แยกจาก free app allowance ไม่ใช่ VM ฟรีสำหรับรัน Docker Compose ทั้งชุด นอกจากนี้ [durable volumes](https://docs.hostless.cloud/billing/costbreakdown) คิดเงินแยกและไม่ใช้ free database-storage pool ห้ามตั้ง PostgreSQL ใน temporary app filesystem เพื่อหลบข้อจำกัด persistence

## ตัวเลือกที่ตัดออกจากรอบนี้

| บริการ | เหตุผล |
|---|---|
| ClawCloud Run | พบ [ประกาศยุติบริการของผู้ให้บริการ](https://question.run.claw.cloud/questions/10010000000003261) ในดัชนีค้นหา และเปิดเว็บ/เอกสารสดไม่สำเร็จ ข้อเสนอเครดิตฟรี GitHub ในหน้าเก่าจึงไม่ใช้แนะนำให้สมัครหรือรับรองสิทธิ์ปัจจุบัน |
| redu.cloud | [หน้า free tier ปัจจุบัน](https://redu.cloud/free) ระบุให้เพิ่มบัตรภายใน 7 วัน มิฉะนั้นลบทรัพยากร จึงไม่ตรงเงื่อนไขไม่ใช้บัตร |
| Prepare.sh | [Free account](https://prepare.sh/premium) พักหลัง session 1 ชั่วโมงและเก็บข้อมูลเพียง 12 ชั่วโมง ไม่ใช่ hosting ต่อเนื่อง |
| NumaVM | [แผนฟรี](https://numavm.com/pricing/) ระบุ RAM 256 MB และเป็น ephemeral/serverless VM ไม่ยืนยันทรัพยากรสำหรับ stack นี้ |
| Runtime | [เครดิตฟรี](https://withruntime.com/docs/included-usage) สำหรับบัญชีที่ยังไม่จ่ายเป็นครั้งเดียวใน 30 วันแรก มีเงื่อนไขภูมิภาคและจำกัด machine-hours ไม่ใช่การจัดสรร VM ฟรีต่อเนื่องตามข้อกำหนด |

ไม่ใช้ข้อเสนอทดสอบ VM อายุ 15–60 นาทีมาแทน hosting ของระบบ และไม่ใช้ traffic ปลอมหรือการสมัครหลายบัญชีเพื่อเลี่ยงโควตา

## ข้อสรุปที่รายงานได้

Hostless เชื่อมบัญชีฟรีและยืนยัน entitlement จากหน้าบัญชีจริงแล้ว จึงพ้นขั้นรออนุญาต sign-in แต่ยังไม่ผ่าน compatibility ของ HEAT ทั้งระบบ: PostGIS, Redis durability, resource fit และพื้นที่เก็บข้อมูลยังไม่มีผลทดสอบจริง การติดตั้ง repo integration ก็ยังไม่ได้ทำ เงื่อนไข zero-cost/no-card/no-local-server/continuous background processing ยังไม่ได้รับการพิสูจน์ทั้งหมด ไม่มีการ deploy ใหม่หรือเปลี่ยน stack เพื่ออ้างว่าข้อจำกัดหมดไปแล้ว
