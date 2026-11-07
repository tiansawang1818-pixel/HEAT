# ตรวจปลายทางฟรีที่ไม่ใช้บัตรเพิ่มเติม

สถานะล่าสุด: เชื่อม Hostless และทดสอบ PostgreSQL/Redis จริงแล้ว ต่อมาผู้ใช้อนุญาตให้ลบ PostgreSQL ทดสอบเพื่อทดลองสร้างใหม่ ตัวเดิมเข้า recovery window และคืน storage ถาวรไม่ได้จนถึง 15 พฤศจิกายน 2026 เวลา 00:29:23 น. กรุงเทพฯ ปัจจุบันมี Redis ทำงานและ PostgreSQL ที่หยุดรอลบ ยังไม่ได้สร้าง PostgreSQL 16 หรือ deploy เว็บ/API/worker ของ HEAT ไม่มีบัตร เครดิต หรือ repo integration ผลแต่ละรอบมี timestamp ในรายงาน ไม่ใช้วันที่บันทึกเอกสารแทนวันที่ข้อมูลสิ่งแวดล้อม

## Hostless: ตัวเลือก PaaS ที่ยังต้องยืนยัน

หน้า [Apps](https://hostless.cloud/products/apps) และ [Databases](https://hostless.cloud/products/databases) ระบุว่าแอปและ worker ไม่พักเมื่อ idle พร้อม managed PostgreSQL/Redis และไม่ต้องใช้บัตรภายในโควตาฟรี จึงเป็นตัวเลือกสำหรับตรวจเพิ่มเติม ไม่ใช่คำรับรองว่า HEAT ทั้ง stack รันฟรีได้แล้ว

[Resource limits](https://docs.hostless.cloud/plans/limits) ระบุนโยบาย shared pool ที่กำลังทยอยใช้: CPU 0.5 vCPU, RAM 1 GiB และ database storage 512 MiB **รวมทั้งบัญชี** เอกสาร [free tier](https://docs.hostless.cloud/plans/free-tier) บางส่วนยังกล่าวถึงพื้นที่ 256 MiB หรือแผนแบบจำกัดจำนวน resource จึงไม่ใช้หน้านั้นแทน entitlement ของบัญชีจริง รอบที่ตรวจยืนยันตัวเลข 0.5 vCPU / 1024 MiB RAM / 512 MiB storage จากทั้งหน้า Current billing และหน้า Account billing ที่มีป้าย Preview ตรงกัน

[ค่าตั้งต้นฐานข้อมูล](https://docs.hostless.cloud/databases/create) คือ CPU 0.25 vCPU, RAM 512 MiB, storage 512 MiB ต่อ database หากเปิด PostgreSQL และ Redis ด้วย default ทั้งคู่ จะใช้ CPU/RAM shared pool ครบและเกิน storage pool ก่อนรวมเว็บ/worker จึงไม่สามารถสรุปว่า stack ปัจจุบันจะพอดีได้จากคำว่า free ต้องตรวจ minimum sizes และวัดการใช้จริงก่อน

รายการที่ยังไม่ยืนยัน:

- managed PostgreSQL ติดตั้ง PostGIS และให้สิทธิ์ `CREATE EXTENSION postgis` ได้หรือไม่ เอกสารที่อ่านยืนยัน PostgreSQL แต่ไม่ได้ยืนยัน PostGIS
- Redis `appendfsync`, connection limits และพฤติกรรมเมื่อใช้หน่วยความจำเต็ม; TLS/logical databases และคิวทดสอบหลัง graceful restart ผ่านแล้ว แต่ไม่ใช้แทนการทดสอบ crash recovery
- RAM/CPU ของ Next.js, API, worker, dispatcher และ Beat รวมกับฐานข้อมูล ภายใต้ resource requests ที่ provider อนุญาตจริง
- การยอมรับ resource configuration ของทั้ง stack ภายใน entitlement ที่ตรวจแล้ว และการป้องกันค่าใช้จ่ายส่วนเกินที่ตรงข้อกำหนดผู้ใช้

### ผลตรวจหน้าบัญชีจริง

ตรวจช่องทางเชื่อมแล้วไม่พบ Hostless plugin ในรายการที่ค้นได้ จึงใช้หน้าเว็บทางการ ผู้ใช้ยืนยันสิทธิ์ที่แสดงและการสร้าง/เข้าบัญชีฟรีภายใต้ [Terms of Service](https://hostless.cloud/terms) และ [Privacy Policy](https://hostless.cloud/privacy) แล้ว จึงกด Authorize และยืนยันอีเมลจนเข้าสู่ dashboard ได้สำเร็จ หน้า Connected accounts แสดง GitHub เป็น Healthy

การเชื่อมยืนยันตัวตนนี้แยกจากการติดตั้ง Hostless GitHub App ให้เข้าถึง source repository หน้าติดตั้งเปิดขึ้นหลัง sign-in แต่ยังไม่ได้ติดตั้งหรือเลือก repository ใด ๆ ไม่เก็บอีเมลส่วนตัว, account IDs, OAuth state, รหัสยืนยัน, credentials หรือภาพบัญชีใน Git

| รายการตรวจจาก UI จริง | ผล |
|---|---|
| บัญชีและ project | มี Redis ทดสอบทำงานหนึ่งตัว และ PostgreSQL ทดสอบใน Recently deleted หนึ่งตัว ไม่มี HEAT app/worker |
| รอบ billing ที่แสดง | 7 พฤศจิกายน – 7 ธันวาคม 2026 |
| Free entitlement | 0.5 vCPU / 1024 MiB RAM / 512 MiB storage รวมทุก project ของบัญชี; Current billing ระบุว่าแชร์ระหว่าง app, worker, cron, database และ Objects |
| การชำระเงิน | ไม่มีบัตร ไม่มีเครดิต ไม่ได้เปลี่ยน spending limit ซึ่งหน้า Preview แสดง No limit หลังทดสอบหน้า Current billing แสดง USD 0.00 และ Collecting usage data ยังไม่ใช่ invoice สรุปหรือการรับรองค่าใช้จ่ายในอนาคต |
| Database engine | ตัวเลือก PostgreSQL, Valkey, Redis, ClickHouse, CouchDB, MongoDB; PostgreSQL ที่เลือกแสดง image `postgres:17` ไม่มีตัวเลือก PostGIS ในรายการที่เปิดตรวจ |
| ขนาด database แบบ Custom | CPU ต่ำสุด 0.2 vCPU, RAM ต่ำสุด 256 MiB, storage ต่ำสุด 256 MiB (`256Mi` ในหน้า Review) |
| Resource admission | เปิด PostgreSQL และ Redis อย่างละ 0.2 vCPU / 256 MiB RAM / 256 MiB storage ได้ โดย quote USD 0.00 ทั้งคู่; การเปิด resource สำเร็จไม่เท่ากับ engine พร้อมใช้งาน |
| Marketplace | รายการ 13 บริการที่แสดงไม่มี PostGIS; ไม่ได้ deploy บริการใด |

หาก PostgreSQL และ Redis ใช้ขนาดต่ำสุดเดียวกัน จะรวมเป็น **0.4 vCPU / 512 MiB RAM / 512 MiB storage** เหลือเพียง 0.1 vCPU / 512 MiB RAM และไม่มีพื้นที่ database pool ที่เหลือ สำหรับ Next.js, API, ingestion worker, control worker และ Beat ใน architecture ปัจจุบัน นี่เป็นการคำนวณ resource requests จากตัวเลขใน UI ไม่ใช่ benchmark และไม่ใช่หลักฐานว่ารันงาน geospatial จริงได้ภายใน RAM/CPU ดังกล่าว

### ผลทดสอบ backend จริง

ดู [รายงานที่ตัดข้อมูลลับออก](verification/hostless-backends-2026-11-07.json) และ [วิธีรันซ้ำ](managed-hosting-checks.md) ผลตรวจ SQL/Redis สุดท้ายในรายงานคือ 7 พฤศจิกายน 2026 เวลา 12:51 UTC (19:51 น. กรุงเทพฯ) ไม่ใช้เวลานี้แทน observation timestamp ของ environmental data

- **PostgreSQL:** endpoint ปิดการเชื่อมต่อ TLS ก่อนเริ่ม SQL (`SSL SYSCALL error: EOF detected`) จึงยังอ่าน `pg_available_extensions` ไม่ได้ หลัง health check และ restart หนึ่งครั้ง หน้า Resources ระบุ `Database restart did not become ready within five minutes` ลองเพิ่มเป็น 0.3 vCPU / 512 MiB RAM / 256 MiB storage ซึ่ง quote ยัง USD 0.00 และรวม Redis แล้วยังไม่เกิน pool แต่ update ยังล้มเหลวด้วย `The database started but did not pass its health check` ไม่มีการปิด TLS verification หรือเปลี่ยนไปใช้ plaintext เพื่อฝืนเชื่อม
- **Redis:** ใช้ endpoint/ACL credentials ที่หน้า dashboard ออกให้จริง ตรวจ certificate/hostname ผ่าน และ `SELECT`/`PING` database 0, 1, 2 ได้ `INFO persistence` แสดง AOF เปิดและสถานะเขียน `ok`
- **คิวหลัง restart:** สร้าง marker และรายการข้อความสองรายการใน namespace สุ่มแยกจากคิว Celery พร้อม TTL 1 ชั่วโมง จากนั้น restart เฉพาะ Redis ทดสอบผ่าน dashboard ตรวจ `run_id` เปลี่ยน และ marker/ลำดับรายการยังครบ ผลนี้พิสูจน์ graceful restart retention ของการทดสอบนี้ ไม่รับรอง abrupt crash, disk failure หรือข้อมูลทุกช่วงเวลา
- **ข้อจำกัด Redis:** ACL ปฏิเสธ `CONFIG GET` จึงยังยืนยัน `appendfsync` ไม่ได้ `INFO memory` แสดง `noeviction` และ `maxmemory=0` ซึ่งไม่ใช่หลักฐานว่ามี RAM ไม่จำกัด ต้องทดสอบ backpressure/cgroup OOM กับโควตาจริงก่อน production
- **Client ของแอป:** หลังเพิ่ม explicit Celery broker/backend SSL settings ทดสอบ broker connection และ result backend PING กับ Redis จริงผ่าน ฝั่งเว็บทดสอบ node-redis ด้วย SNI/hostname verification และ database 2 ได้ PONG ไม่มีการเริ่ม web/API server, worker หรือ scheduler ในเครื่องผู้ใช้

การทดสอบกู้ PostgreSQL ใช้ resource requests รวมกับ Redis เป็น 0.5 vCPU / 768 MiB RAM / 512 MiB storage จึงไม่เหลือ CPU request สำหรับเพิ่ม HEAT runtime ตาม configuration นี้ นี่เป็นขนาดเพื่อวินิจฉัย ไม่ใช่ deployment plan ที่ผ่าน load/capacity test ไม่มีการเพิ่ม storage, paid volume, trial, บัตร หรือเครดิต

### การทดลองสร้างใหม่และข้อจำกัด retention

ผู้ใช้เลือก **ยังไม่ส่งข้อความหาฝ่ายช่วยเหลือ** จึงเก็บ [ร่างข้อความ](hostless-readiness-support-draft.md) ไว้ ไม่ส่งให้บุคคลภายนอก หลังผู้ใช้ยืนยันการลบเฉพาะฐานข้อมูลทดสอบ `heat-compatibility-check` รวมการลบถาวรหากทำได้ จึงลบเข้าช่วง recovery แล้ว แต่ dashboard ระบุว่าปุ่ม `Delete Permanently` ใช้ได้หลัง recovery period เท่านั้น กำหนดคือ **15 พฤศจิกายน 2026 เวลา 00:29:23 น. กรุงเทพฯ** ไม่ force-click หรือเรียก API เพื่อข้ามข้อจำกัดนี้

เตรียม PostgreSQL 16 ชื่อ `heat-pg16-check` ขนาด 0.2 vCPU / 256 MiB RAM / 256 MiB storage เพื่ออ่าน quote แล้ว **ไม่ได้กด Deploy** แม้หน้าฟอร์มแสดง `This resource total $0.00` และบอกว่า configuration อยู่ใน free limits แต่รายการ Recently deleted ยังบอกว่า storage 256 MiB ของตัวเดิมถูกเก็บและยัง billable หากรวมกับ Redis 256 MiB และตัวใหม่อีก 256 MiB จะเป็น 768 MiB ซึ่งเกิน pool 512 MiB จึงไม่ใช้ quote เฉพาะ resource เป็นหลักฐานว่าบิลทั้งบัญชีเป็นศูนย์ ต้องให้พื้นที่รอลบถูกคืนหรือมีคำยืนยันเงื่อนไขที่ไม่ขัดกันก่อนเปิดใหม่

ก่อนลบ ตรวจหน้า Storage พบ PostgreSQL ใช้ 18.0% ของความจุ และหน้า Metrics แสดง Engine CPU ประมาณ 0.01 vCPU แต่ Engine memory เป็น unavailable จึงไม่มีหลักฐานให้เพิ่ม storage หรือสรุปว่า RAM เต็ม การตรวจ TLS แบบไม่ส่ง credentials พบ SSLRequest response `S` แต่ handshake ล้มเหลวทั้ง STARTTLS และ direct TLS ที่ใช้ SNI/ALPN `postgresql` ดู [รายงาน](verification/hostless-postgres-tls-diagnostic.json) ไม่ปิดการตรวจ certificate หรือทดลองส่งรหัสผ่านผ่าน plaintext

การตรวจความจุที่ทำได้โดยไม่รอ cloud: [CI baseline ทั้ง 7 services](capacity-validation.md#ผลที่วัดแล้ว) วัด RAM รวมพร้อมกันสูงสุดประมาณ 664.84 MiB และ anonymous memory ประมาณ 499.13 MiB ผ่านครบ 5 CI jobs แต่ยังไม่มี peak ingestion/optimizer หรือ provider resource admission จึงไม่ยกสถานะ hosting เป็นพร้อมใช้

ไม่พบตัวเลือก PostGIS **ไม่เท่ากับพิสูจน์ว่า extension ใช้ไม่ได้** แต่ฐานข้อมูลยังไม่พร้อมให้ตรวจ SQL จึงถือว่าด่านนี้ไม่ผ่าน ต้องได้ engine ที่ healthy แล้วตรวจ extension และ spatial query จริง ไม่ย้าย migrations หรือ deploy เว็บเปล่าเพื่ออ้างว่าระบบครบแล้ว Operational logs ของฐานข้อมูลยังไม่ได้เปิด collection; ไม่ส่งข้อความหาผู้ให้บริการแทนเจ้าของโดยไม่มีคำสั่ง

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

Hostless ผ่านขั้นบัญชี, entitlement, resource admission ที่เคยทดสอบ และ Redis TLS/logical databases/graceful restart retention แล้ว มี CI baseline ทรัพยากรจริงเพิ่มเติม แต่ **ยังไม่ผ่าน PostgreSQL readiness/PostGIS, Redis crash durability และ whole-stack capacity** ขณะนี้ติดพื้นที่ PostgreSQL รอลบก่อนทดลอง instance ใหม่ภายใน pool ได้ การติดตั้ง repo integration ยังไม่ได้ทำ ไม่มี production deployment และไม่รับรองว่ารัน HEAT ทั้งชุดได้ฟรีจาก quote เฉพาะ resource หรือยอด billing ชั่วขณะ
