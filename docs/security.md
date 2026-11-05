# Security and production checklist

Local Compose ใช้ shared admin key เพื่อให้เริ่มระบบได้ง่ายเท่านั้น Production backend ปฏิเสธ admin operation ทุกกรณีจนกว่าจะตั้ง `ADMIN_AUTH_MODE=oidc` พร้อม issuer, audience, JWKS URL และ role ที่อนุญาต

การป้องกันที่มีแล้วสำหรับ local/pilot: backend เปรียบเทียบ admin secret แบบ constant-time, sanitize `X-Admin-Actor` ก่อนเขียน audit trail และ Next.js server proxy ไม่เปิดเผย backend key ต่อ browser พร้อมจำกัด body ของคำขอ admin ส่วนการอนุมัติต้นทุน/candidate สร้าง `PipelineEvent` ที่ตรวจสอบย้อนหลังได้

OIDC mode ตรวจลายเซ็นจาก JWKS, allowlist asymmetric algorithms, `iss`, `aud`, `exp`, `sub` และ role claim; JWKS cache มี TTL และ production บังคับ HTTPS actor ใน audit trail มาจาก verified `preferred_username`, `email` หรือ `sub` เท่านั้น ไม่เชื่อ `X-Admin-Actor` จาก client ในโหมดนี้ Next.js proxy รับ access token เฉพาะใน request แล้ว forward เป็น bearer โดยไม่บันทึก/ส่งกลับ response ระบบยังไม่สร้าง login/session เอง องค์กรต้องผูกกับ IdP/SSO flow ที่ได้รับอนุมัติและกำหนด token lifetime/rotation ตามนโยบาย

- ปิด `ALLOW_FIXTURE_INGESTION` และ `NEXT_PUBLIC_DEMO_MODE` ใน production
- เก็บ `ADMIN_API_KEY` และ `DASHBOARD_ADMIN_TOKEN` เป็น server-side secrets เท่านั้น ห้ามใช้ชื่อ `NEXT_PUBLIC_*`
- เก็บ provider credentials ใน secret manager/workload identity; rotate และกำหนด least privilege
- TLS ทุก hop ภายนอก trusted network; ตั้ง secure headers และ proxy-aware client IP
- จำกัด CORS เป็น origin จริง ห้าม `*` สำหรับ admin API
- เปิด `RATE_LIMIT_ENABLED=true` เมื่อ Redis พร้อม ระบบใช้ fixed-window counter แบบ atomic แยก assistant, optimization, scenario, forecast และ admin; key เก็บ SHA-256 digest ของ client address ไม่เก็บ raw address งาน public fail-open เมื่อ Redis ขัดข้องเพื่อรักษา map availability แต่งาน admin fail-closed โดยค่าเริ่มต้น
- validate bbox/page size/budget/date range เพื่อป้องกัน expensive query abuse
- fixture path ต้อง resolve ภายใน configured `FIXTURE_ROOT`; ห้ามรับ arbitrary filesystem path
- ตรวจ ZIP/path traversal, SSRF และ content type หากรองรับ upload ในอนาคต
- SQL query ใช้ parameterization; geometry ต้องมี vertex/size limits และ validity check
- SSE connection มี connection/heartbeat limit และไม่เผย admin-only event payload
- log แบบ structured แต่ redact API key, authorization, cookie, signed URL และ personal data
- image/container pin digest และสแกน dependency/CVE ใน CI
- backup, restore, retention และ incident response ต้องทดสอบ

ระบบอาจประมวลข้อมูล vulnerability ที่ละเอียดอ่อนในอนาคต ให้ทำ privacy/ethics review, aggregation threshold และ access control ก่อนเชื่อมข้อมูลระดับบุคคล HEAT//SHIFT ไม่ต้องการข้อมูลระบุตัวบุคคลสำหรับ scoring ระดับพื้นที่

หากอยู่หลัง reverse proxy ให้ตั้ง trusted proxy/IP handling ที่ infrastructure layer อย่างชัดเจน มิฉะนั้น rate limiter จะเห็น IP ของ proxy รวมกัน ห้ามเชื่อ `X-Forwarded-For` จากอินเทอร์เน็ตโดยตรง
