# ตรวจปลายทางฟรีที่ไม่ใช้บัตรเพิ่มเติม

วันที่ตรวจ: 7 พฤศจิกายน 2026 — ยังไม่ได้สมัครผู้ให้บริการ เปิดเครื่อง เพิ่มบัตร หรือส่งข้อมูล/credentials ของ HEAT ไปยังผู้ให้บริการเหล่านี้ การพบข้อเสนอฟรียังไม่เท่ากับได้รับทรัพยากรที่ใช้งานได้จริง

## Hostless: ตัวเลือก PaaS ที่ยังต้องยืนยัน

หน้า [Apps](https://hostless.cloud/products/apps) และ [Databases](https://hostless.cloud/products/databases) ระบุว่าแอปและ worker ไม่พักเมื่อ idle พร้อม managed PostgreSQL/Redis และไม่ต้องใช้บัตรภายในโควตาฟรี จึงเป็นตัวเลือกสำหรับตรวจเพิ่มเติม ไม่ใช่คำรับรองว่า HEAT ทั้ง stack รันฟรีได้แล้ว

[Free allowance](https://docs.hostless.cloud/plans/free-tier) และ [resource limits](https://docs.hostless.cloud/plans/limits) ระบุนโยบาย shared pool ที่กำลังทยอยใช้: CPU 0.5 vCPU, RAM 1 GiB และ database storage 512 MiB **รวมทั้งบัญชี** เอกสารเดิมบางหน้ายังกล่าวถึงแผนแบบจำกัดจำนวน resource และพื้นที่ 256 MiB ต้องยืนยัน policy ของบัญชี/รอบ billing จริง ไม่ใช้ข้อความโฆษณาแทนการตรวจ entitlement

[ค่าตั้งต้นฐานข้อมูล](https://docs.hostless.cloud/databases/create) คือ CPU 0.25 vCPU, RAM 512 MiB, storage 512 MiB ต่อ database หากเปิด PostgreSQL และ Redis ด้วย default ทั้งคู่ จะใช้ CPU/RAM shared pool ครบและเกิน storage pool ก่อนรวมเว็บ/worker จึงไม่สามารถสรุปว่า stack ปัจจุบันจะพอดีได้จากคำว่า free ต้องตรวจ minimum sizes และวัดการใช้จริงก่อน

รายการที่ยังไม่ยืนยัน:

- managed PostgreSQL ติดตั้ง PostGIS และให้สิทธิ์ `CREATE EXTENSION postgis` ได้หรือไม่ เอกสารที่อ่านยืนยัน PostgreSQL แต่ไม่ได้ยืนยัน PostGIS
- Redis persistence/queue recovery, connection limits และ session storage ที่เหมาะสมกับ Celery/SSO
- RAM/CPU ของ Next.js, API, worker, dispatcher และ Beat รวมกับฐานข้อมูล ภายใต้ resource requests ที่ provider อนุญาตจริง
- entitlement ของบัญชีใหม่และการป้องกันค่าใช้จ่ายส่วนเกินที่ตรงข้อกำหนดผู้ใช้

### ผลตรวจหน้าบัญชีจริง

ตรวจช่องทางเชื่อมแล้วไม่พบ Hostless plugin ในรายการที่ค้นได้ จึงเปิดเว็บ Hostless แบบ read-only พบว่ายังไม่มี session ที่เข้าสู่ระบบ เริ่ม normal GitHub sign-in เพื่ออ่านหน้าขอสิทธิ์ แต่ **ไม่ได้กด Authorize**

หน้าขอสิทธิ์ระบุ Hostless App โดย Hostless Cloud ขอการยืนยันตัวตน GitHub อ่านอีเมล และทำงานในนามบัญชีตามสิทธิ์ของแอป ยังไม่ได้ติดตั้ง app ให้เข้าถึง repository หรือเปิด resource ข้อมูล OAuth state, credentials และภาพบัญชีไม่เก็บใน Git

การตรวจ entitlement/ฐานข้อมูลจริงต่อจึงติดที่การอนุญาตเชื่อมบัญชีใหม่ ต้องได้รับความยินยอมจากเจ้าของบัญชีก่อน การสร้างหรือเข้าบัญชีอยู่ภายใต้ [Terms of Service](https://hostless.cloud/terms) และ [Privacy Policy](https://hostless.cloud/privacy) ของผู้ให้บริการ ไม่ใช้คำสั่งให้ทำงานทั้งหมดเป็นการอนุญาตให้ยอมรับสิทธิ์บัญชี/สัญญาใหม่โดยอัตโนมัติ

หลังอนุญาตจึงตรวจแผนที่บัญชีได้รับและ resource quotes ก่อน provision ใด ๆ และตรวจ PostGIS/Redis persistence ด้วยการทดสอบที่ได้รับอนุญาต ไม่รายงานว่ามีเครื่องแล้วจากการเข้าสู่ระบบเพียงอย่างเดียว

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

Hostless เป็น candidate สำหรับตรวจ PaaS ต่อ ยังไม่ใช่เครื่องที่ยืนยันสิทธิ์และ compatibility ครบ เงื่อนไข zero-cost/no-card/no-local-server/continuous background processing ของ HEAT ยังไม่ได้รับการพิสูจน์ทั้งหมด ไม่มีการ deploy ใหม่หรือเปลี่ยน stack เพื่ออ้างว่าข้อจำกัดหมดไปแล้ว
