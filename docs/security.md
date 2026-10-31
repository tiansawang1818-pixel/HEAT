# Security and production checklist

Local Compose ใช้ shared admin key เพื่อให้เริ่มระบบได้ง่ายเท่านั้น ก่อนเปิดใช้งานภายนอกให้เปลี่ยนเป็น OIDC/SSO + RBAC และ audit log ที่ผูกกับผู้ใช้จริง

- ปิด `ALLOW_FIXTURE_INGESTION` และ `NEXT_PUBLIC_DEMO_MODE` ใน production
- เก็บ `ADMIN_API_KEY` และ `DASHBOARD_ADMIN_TOKEN` เป็น server-side secrets เท่านั้น ห้ามใช้ชื่อ `NEXT_PUBLIC_*`
- เก็บ provider credentials ใน secret manager/workload identity; rotate และกำหนด least privilege
- TLS ทุก hop ภายนอก trusted network; ตั้ง secure headers และ proxy-aware client IP
- จำกัด CORS เป็น origin จริง ห้าม `*` สำหรับ admin API
- rate-limit assistant, optimization, forecast และ ingestion endpoints แยกกัน
- validate bbox/page size/budget/date range เพื่อป้องกัน expensive query abuse
- fixture path ต้อง resolve ภายใน configured `FIXTURE_ROOT`; ห้ามรับ arbitrary filesystem path
- ตรวจ ZIP/path traversal, SSRF และ content type หากรองรับ upload ในอนาคต
- SQL query ใช้ parameterization; geometry ต้องมี vertex/size limits และ validity check
- SSE connection มี connection/heartbeat limit และไม่เผย admin-only event payload
- log แบบ structured แต่ redact API key, authorization, cookie, signed URL และ personal data
- image/container pin digest และสแกน dependency/CVE ใน CI
- backup, restore, retention และ incident response ต้องทดสอบ

ระบบอาจประมวลข้อมูล vulnerability ที่ละเอียดอ่อนในอนาคต ให้ทำ privacy/ethics review, aggregation threshold และ access control ก่อนเชื่อมข้อมูลระดับบุคคล HEAT//SHIFT ไม่ต้องการข้อมูลระบุตัวบุคคลสำหรับ scoring ระดับพื้นที่
