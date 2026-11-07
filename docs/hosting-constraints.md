# เงื่อนไขการเปิดระบบ HEAT//SHIFT

ยืนยันโดยผู้ใช้วันที่ 6 พฤศจิกายน 2026 (Asia/Bangkok):

- ไม่เสียเงิน และไม่เปิดทรัพยากรที่คิดเงินเมื่อเกินโควตา
- ไม่ใช้เครื่องของผู้ใช้เป็นเซิร์ฟเวอร์หรือ background worker
- ไม่มีบัญชี cloud ในปัจจุบัน และไม่ใช้บัตรเพื่อสมัครหรือยืนยันตัวตน
- ต้องมีงานอัปเดตข้อมูลต่อเนื่อง ไม่แทนระบบเต็มด้วยบริการที่พักตัวหรือหน้าเว็บสาธิต

การแก้ไข source code ใน workspace และการตรวจด้วย CI ไม่ใช่การนำเครื่องผู้ใช้ไปใช้เป็นเซิร์ฟเวอร์ ห้ามเริ่มแอป/worker/scheduler ในเครื่องเพื่อทดแทน hosting โดยไม่มีคำขอใหม่จากผู้ใช้

## ผลตรวจตัวเลือก

| ตัวเลือก | ข้อจำกัดที่กระทบเงื่อนไข | สถานะ |
|---|---|---|
| Render Free | Web service พักหลังไม่มี inbound traffic 15 นาที; background worker และ cron service ไม่มี Free instance | ยังไม่ตรงงานต่อเนื่องทั้งระบบ |
| Vercel Hobby | เหมาะกับเว็บส่วนบุคคล noncommercial; ไม่ใช่ Linux VM สำหรับ Docker/PostGIS/Celery stack | ใช้แทนระบบทั้งหมดไม่ได้ |
| Supabase Free | ให้ PostgreSQL แต่ไม่ใช่เครื่องรัน FastAPI/Celery; project อาจพักหลัง activity ต่ำ | ใช้แทนระบบทั้งหมดไม่ได้ |
| GitHub Actions schedule | งานอาจล่าช้าหรือถูกทิ้งขณะโหลดสูง และ scheduled workflow ใน public repo อาจถูกปิดหลังไม่มี activity 60 วัน | ไม่รับรองความต่อเนื่องหรือเวลาเริ่มที่แน่นอน |
| Oracle Always Free VM | อาจรัน background services ได้ แต่ต้องได้ capacity จริง; การสมัครส่วนใหญ่มักต้องใช้บัตรและ idle VM อาจถูกเรียกคืน | ยังไม่มีทรัพยากรที่ยืนยันได้ และเงื่อนไขบัตรไม่ตรงความต้องการ |
| Koyeb Free | ให้ web instance เดียว ไม่ให้ worker/volume และพักเมื่อไม่มี traffic 1 ชั่วโมง | ไม่รองรับ stack และงานต่อเนื่องตามเงื่อนไข |
| Northflank Developer Sandbox | เอกสาร billing ระบุว่าผู้ใช้ทุกแผนต้องเพิ่มวิธีชำระเงินก่อนสร้างทรัพยากร แม้มี free sandbox และไม่แนะนำ sandbox สำหรับ production | ยังไม่ตรงเงื่อนไขไม่ใช้บัตร/วิธีชำระเงิน ไม่เปิดบัญชีหรือ resources |

แหล่งทางการที่ตรวจ:

- [Render free instances](https://render.com/docs/free)
- [Vercel Hobby](https://vercel.com/docs/plans/hobby)
- [Supabase project pausing](https://supabase.com/docs/guides/platform/free-project-pausing)
- [GitHub scheduled workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [GitHub automatic schedule disabling](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows)
- [Oracle Free Tier registration](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm)
- [Oracle Always Free capacity and idle reclamation](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [Koyeb Free instance limitations](https://www.koyeb.com/docs/reference/instances)
- [Northflank billing and free sandbox requirements](https://northflank.com/docs/v1/application/billing/pricing-on-northflank)

ยังไม่มีปลายทางที่ผ่านเงื่อนไขทั้งหมด ห้ามรายงานว่า deploy แล้วหรือรับรองว่ารันฟรีตลอด ทางเดินที่ต้องตรวจต่อคือทรัพยากรฟรีที่มีเจ้าของอนุญาตให้ใช้จริง เช่น server/cloud allocation ขององค์กร มหาวิทยาลัย หรือผู้สนับสนุน โดยต้องยืนยันสิทธิ์ ค่าใช้จ่าย architecture และการรันงานเบื้องหลังของทรัพยากรนั้นก่อน

ตรวจ Koyeb/Northflank เพิ่มเติมวันที่ 7 พฤศจิกายน 2026 ยึดเอกสารข้อกำหนด instance/billing แทนคำโฆษณาว่า “free/always-on” ไม่มีการสมัคร เพิ่มวิธีชำระเงิน หรือเปิด trial

ตรวจผู้ให้บริการเพิ่มเติมใน [no-card hosting research](no-card-hosting-research.md): บัญชี Hostless ได้ entitlement 0.5 vCPU / 1024 MiB RAM / 512 MiB storage รวมทั้งบัญชี เปิด PostgreSQL/Redis ทดสอบภายในโควตาแล้ว Redis ผ่าน TLS, logical DB 0/1/2 และคิวทดสอบยังอยู่หลัง restart จริง แต่ PostgreSQL ไม่ผ่าน health check แม้ลองเพิ่ม CPU/RAM ภายใน pool จึงยังตรวจ PostGIS และ whole-stack capacity ไม่ได้ Redis ยังมีข้อจำกัด CONFIG ACL/การตรวจ crash durability ไม่มีบัตร เครดิต หรือ repo integration และยังไม่มี HEAT app/worker production ส่วน ClawCloud พบประกาศยุติบริการ จึงไม่ใช้ข้อเสนอเก่ามาแนะนำให้สมัคร

ห้ามเปิด Pay As You Go, เริ่ม free trial ที่หมดอายุแล้วอาจคิดเงิน, ใส่บัตร, สร้าง load ปลอมเพื่อเลี่ยง idle policy หรือเปลี่ยนไปใช้เครื่องผู้ใช้แทน เงื่อนไขผู้ให้บริการอาจเปลี่ยนได้ ต้องตรวจอีกครั้งก่อนเปิดทรัพยากรจริง

## งานที่ทำต่อได้โดยไม่ต้องรอผู้ใช้

- เพิ่มงานนำเข้าข้อมูลย้อนหลังหลายปีแบบจำกัดจำนวน asset และมี checkpoint โดยไม่เรียก Earth Engine จริงจนมีสิทธิ์ที่ตรวจแล้ว
- ทดสอบความถูกต้องของการนำเข้าย้อนหลัง การคง observation เดิม การอัปเดต client และข้อจำกัด optimizer ผ่าน CI
- ตรวจความเข้ากันได้ของ container architecture และเตรียม deployment checks โดยยังไม่เปิดทรัพยากร
- ปรับข้อความ/การแสดงข้อจำกัดภาษาไทย และค้นเอกสารต้นทุนสาธารณะที่มีแหล่งอ้างอิงเพื่อให้ผู้รับผิดชอบตรวจ
- อัปเดตคู่มือและ commit/push เข้า HEAT ตามคำสั่งที่ได้รับแล้ว

ความคืบหน้า 7 พฤศจิกายน 2026: เพิ่ม historical ingestion ที่บันทึก cursor/budget/cancel/resume พร้อมหน้าควบคุมภาษาไทยแล้ว ดู [คู่มือ](historical-ingestion.md) และปิดการเรียกจริงไว้ตามเดิม เพิ่มการรักษา immutable decision snapshots, scenario baseline ตามหลักฐาน, affected-layer client tests และการตรวจ native ARM พร้อม [แหล่งต้นทุนสาธารณะสำหรับตรวจ](cost-evidence-sources.md) การผ่าน CI ไม่แทนสิทธิ์ provider/hosting หรือการสำรวจหน้างานด้านล่าง

## งานที่ต้องมีทรัพยากร สิทธิ์ หรือการรับรองจากเจ้าของจริง

- เครื่องฟรีที่อนุญาตให้รันระบบต่อเนื่อง พร้อมบัญชีหรือสิทธิ์ที่ใช้เข้าถึงได้
- บัญชี Earth Engine ที่ผ่านการยืนยันประเภทการใช้งานและโควตา และบัญชีผู้ดูแล/SSO สำหรับระบบจริง
- หลักฐานสำรวจพื้นที่ สิทธิ์ใช้พื้นที่ และการอนุมัติต้นทุน/ความเหมาะสมของโครงการ ไม่ใช้ข้อมูลสมมติแทนการรับรอง
- การตรวจบน hosting/ข้อมูลจริงก่อนเปิดใช้งาน ซึ่งทำต่อได้เมื่อมีทรัพยากรและสิทธิ์ข้างต้น
