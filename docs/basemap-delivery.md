# แผนที่พื้นฐานและสิทธิ์ใช้ tile

ค่าเริ่มต้น `/styles/community-osm.json` เป็น MapLibre style ที่ HEAT//SHIFT ให้บริการจาก origin ของเว็บเอง แล้ว browser ขอ raster tile จริงจาก OpenStreetMap ตามพื้นที่ที่มองเห็น ไม่ใช่ภาพแผนที่จำลอง ไม่ต้องใช้ CARTO API key หรือบัญชี basemap ใหม่ ขอบเขต/ข้อมูลวิเคราะห์ยังมาจาก API ที่ผ่าน validation แยกจากพื้นถนนและชื่อสถานที่

OpenStreetMap data มีเงื่อนไขลิขสิทธิ์ของข้อมูล ส่วนเซิร์ฟเวอร์ tile เป็นบริการชุมชนแบบ best effort ไม่มี SLA และไม่รับประกันรองรับเว็บที่มี traffic สูง การใช้พื้นฐานนี้จึงไม่ได้แก้ปัญหา hosting ต่อเนื่องของ API/worker ต้องประเมิน traffic และสิทธิ์ใช้ provider อีกครั้งก่อนเปิดจริง

- Attribution และลิงก์รายงานปัญหาต้องเห็นได้บนแผนที่ทุกแผนที่ ไม่ซ่อนในปุ่มยุบหรือใต้ overlay
- Browser ส่ง Referer ตาม `strict-origin-when-cross-origin` และใช้ browser User-Agent ปกติ ไม่ปลอมตัวหรือปิด cache HTTP
- ไม่เพิ่ม offline maps, bulk downloading, tile prefetch, bot panning หรือ screenshot crawler ที่กวาดพื้นที่ กรณีทดสอบอัตโนมัติใช้ style ภายใน/fixture โดยไม่ดึง tile จำนวนมาก
- ไม่บังคับ `no-cache` สำหรับ tile; ให้ browser เคารพ Cache-Control/Expires/ETag ของ provider
- เปลี่ยน `NEXT_PUBLIC_MAP_STYLE_URL` เป็น style HTTPS ที่เจ้าของอนุญาตได้เมื่อ traffic โต ค่า frontend นี้ฝังตอน build จึงต้อง rebuild

การมี public URL ของ style จากผู้ให้บริการอื่นไม่ได้แปลว่าใช้งานโดยไม่มี API key ได้ ตรวจสัญญาและ attribution ของ provider นั้นก่อนเสมอ

อ้างอิง [OSM Tile Usage Policy](https://operations.osmfoundation.org/policies/tiles/) และ [OSM copyright](https://www.openstreetmap.org/copyright)
