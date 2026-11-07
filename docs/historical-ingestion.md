# นำเข้าข้อมูลย้อนหลังอย่างจำกัดและทำต่อได้

งานนี้เตรียม code และชุดทดสอบ ไม่ได้เรียก Earth Engine ของผู้ใช้จริงหรือยืนยันสิทธิ์แทนเจ้าของบัญชี ค่าเริ่มต้น `HISTORICAL_INGESTION_ENABLED=false` จนกว่าผู้รับผิดชอบตรวจ project สิทธิ์ ประเภทการใช้ โควตา และเงื่อนไขค่าใช้จ่ายเรียบร้อย การเปิด flag ไม่เปิด billing plan ใดให้ผู้ใช้

## การทำงาน

- ผู้ดูแล SSO สร้างงานผ่าน Data Observatory หรือ `POST /api/ingestion/backfills` ระบุ `source`, `start_date`, `end_date` (ไม่รวมวันสิ้นสุด), `max_assets`, `max_catalogue_requests`, `step_delay_seconds`, `page_size`, `window_days`
- รองรับ Landsat 8/9, Sentinel-2 และ Dynamic World ช่วงเวลาไม่เกิน 3,660 วันและไม่เกินวันปัจจุบัน แบ่งเป็นหน้าสูงสุด 25 ภาพและช่วงย่อยสูงสุด 32 วัน
- แต่ละ delivery ตรวจรายการได้สูงสุดหนึ่งครั้งและประมวลผลภาพได้สูงสุดหนึ่งครั้ง โดยใช้ขอบเขตทางการทั้งหมดที่มี ไม่ยึด source asset ID สำหรับพื้นที่ย่อยโดยพลการ
- เรียง `system:index` ภายในแต่ละ original collection ใช้ `ee.Filter.gt` ทำ keyset pagination พร้อมเก็บ cursor, pending assets, counters และเวลาเริ่มรอบถัดไปใน PostgreSQL เดินทั้ง Landsat 8 และ 9 โดยคง image ID จริงสำหรับ processing/tiles
- นับคำขอรายการและความพยายามประมวลผล **ก่อนเรียก provider** การลองใหม่ใช้งบจำนวนครั้งเดียวกัน ไม่มีการเพิ่มเพดานอัตโนมัติ เพดานนี้ไม่ใช่ EECU/byte/billing cap ต้องกำหนด hard quota ในบัญชี provider แยกต่างหาก
- Celery Beat dispatcher ส่งงานต่อเมื่อถึงกำหนด ไม่มี HTTP/background fallback ใน API ใช้คิวถาวร Redis, source/job locks, delivery generations และ lease recovery ป้องกันงานซ้ำ เมื่อ worker ขัดข้องจะทำต่อจากข้อมูลที่ commit แล้ว
- ประมวลผลผ่าน validation และ atomic publication เดิม ภาพไม่ผ่านเกณฑ์ถูกเก็บเหตุผล ภาพล้มเหลวประมวลผลลองใหม่ได้แบบจำกัด ย้อนหลังไม่เปลี่ยน `last system check` ของการตรวจหาข้อมูลล่าสุด และ observation time ยังคุม active map snapshot
- `GET /api/ingestion/backfills` แสดงงาน 20 รายการล่าสุดและเพดานจริง ส่วน timeline/child jobs อยู่ใน ingestion APIs เดิม

`POST /api/ingestion/backfills/{id}/cancel` หยุดหลัง atomic asset ปัจจุบันเสร็จ ไม่ยกเลิกครึ่ง transaction `POST .../{id}/resume` รับเฉพาะเพดานรอบใหม่ สร้าง job ID ใหม่จาก frozen cursor/pending เดิม และไม่เปลี่ยนผลหรือ config ของงานเก่า งานที่ค้นครบแล้วไม่ต้อง resume

## ตัวอย่าง body (ยังไม่สั่งเรียก provider)

```json
{
  "source": "landsat",
  "start_date": "2020-01-01",
  "end_date": "2025-01-01",
  "max_assets": 12,
  "max_catalogue_requests": 50,
  "step_delay_seconds": 600,
  "page_size": 10,
  "window_days": 16
}
```

แต่ละแหล่งมีงานย้อนหลัง active ได้หนึ่งงาน Source monitor ปกติใช้ source lock เดียวกัน จึงไม่ publish พร้อมกัน หากส่งคิวไม่สำเร็จ dispatcher จะลองส่งใหม่โดยไม่เพิ่ม provider calls; lease ที่ค้างจาก worker หายกู้คืนหลัง hard deadline และ shutdown margin (65 นาที)

## ข้อจำกัดทางวิทยาศาสตร์

ประวัติหลายปีไม่ได้แปลว่าข้อมูลครบทุกเดือนหรือทุกเขต เมฆ พิกเซลหาย ความเปลี่ยนแปลงขอบเขต และฤดูกาลมีผลต่อการเปรียบเทียบ NDVI/อุณหภูมิ ต้องดู valid coverage, observation time และ validation reports เสมอ แบบจำลองอนาคตยังต้องผ่าน temporal/spatial holdout และ baseline guard เดิม งานนี้ไม่สร้างตัวเลขอุณหภูมิ ต้นทุน หรือผลลดความร้อนสมมติในระบบจริง

อ้างอิง provider: [ImageCollection.sort](https://developers.google.com/earth-engine/apidocs/ee-imagecollection-sort), [ImageCollection.limit](https://developers.google.com/earth-engine/apidocs/ee-imagecollection-limit), [Filter.gt](https://developers.google.com/earth-engine/apidocs/ee-filter-gt)

ชุดทดสอบใช้ synthetic provider fixture ในฐานข้อมูล CI แยก ไม่มีการเข้าบัญชีดาวเทียมหรือเปิด credentials จริง ทดสอบงบจำนวนครั้ง การทำต่อ งานซ้ำ ล้มเหลว/ลองใหม่ การยกเลิก และการคง active observation/last source check
