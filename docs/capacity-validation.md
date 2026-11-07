# ตรวจทรัพยากรโดยไม่ใช้เครื่องผู้ใช้เป็นเซิร์ฟเวอร์

`scripts/profile_compose_capacity.py` รันได้เฉพาะ isolated GitHub Actions CI ไม่เริ่ม container เอง และไม่อ่าน environment หรือ credentials ของ container

CI เริ่ม PostgreSQL, Redis, API, Next.js, ingestion worker, control dispatcher และ Beat จาก production Compose ในฐานข้อมูลชั่วคราว หลังจบการตรวจ release ปกติแล้ว ปิด `DataSource.enabled` ทุกแหล่งก่อนเริ่ม scheduler และตรวจว่าไม่มี `SourceCheck` เกิดขึ้นระหว่างวัด ไม่ใช้สิทธิ์บัญชี Earth Engine จริง ไม่แสดง test environment เป็นข้อมูลสิ่งแวดล้อมที่วัดจริง

เมื่อปิดแหล่งข้อมูลทั้งหมด `/api/health/ready` จะตอบ 503 ตามนโยบายเดิม จึงใช้ `/api/health` ตรวจการเชื่อมฐานข้อมูลใน baseline นี้ และรายงาน `application_readiness_verified: false` ไม่เปลี่ยน readiness gate ของแอปให้ยอมรับการติดตั้งที่ไม่มีแหล่งข้อมูล

การวัด:

- อุ่นเครื่องด้วย GET ที่ตรวจ TLS ของหน้าเว็บและ API แบบจำกัดจำนวน
- เก็บ cgroup v2 ของทั้ง 7 services ในแต่ละรอบ รวม memory ปัจจุบัน, anonymous memory, file cache, CPU time และ OOM-kill counters
- รวม memory ของ service จากรอบเดียวกัน ไม่บวก peak จากคนละเวลาแล้วอ้างว่าเป็น peak พร้อมกัน
- เปรียบเทียบกับ RAM pool 1 GiB ที่ยืนยันจากบัญชี Hostless แต่ไม่ใช้การวัดบน CI แทน provider resource admission

ผลที่อาจได้:

| Status | ความหมาย |
|---|---|
| `baseline_anonymous_memory_exceeds_allowance` | Anonymous memory ของ baseline ที่วัดเกิน pool แล้ว ไม่สามารถแก้ผลนี้ด้วยการนับ file cache เป็นพื้นที่ว่าง |
| `baseline_memory_pressure_exceeds_allowance` | Memory รวมในรอบวัดเกิน pool แต่ anonymous memory ยังไม่เกิน ต้องแยก reclaimable cache และวัดภายใต้ข้อจำกัดจริง |
| `production_fit_not_established` | Baseline ที่วัดต่ำกว่า pool ยังไม่พิสูจน์ peak ระหว่างนำเข้า/optimization/training หรือการรับ configuration ของ provider |

ทุกสถานะยังคง `complete_hosting_fit_verified: false` สคริปต์คืน 0 เมื่อเก็บ baseline ได้ครบ ไม่ใช่อนุมัติให้ deploy หากเก็บข้อมูลบริการใดไม่ครบจะล้มเหลว ไม่แทนค่าที่หายด้วยศูนย์

รายงานอยู่ใน artifact `release-verification-reports` ชื่อ `heatshift-capacity-profile.json` พร้อม SHA ที่วัด รายงานนี้ไม่รวม Caddy/platform edge หรือ monitoring services ที่เป็นทางเลือก Current production ใช้ Earth Engine asset references จึงไม่เริ่ม local MinIO ใน profile นี้

## ผลที่วัดแล้ว

[CI run 42396259747](https://github.com/tiansawang1818-pixel/HEAT/actions/runs/42396259747) ผ่านทั้ง 5 jobs และเก็บ [baseline report](verification/compose-capacity-baseline.json) จากโค้ด `bf092f4cdcadff5f9528293c6e1c023e5969c7dd` ได้ครบ 10 รอบบน x86_64:

- Memory รวมสูงสุดที่วัดพร้อมกันประมาณ **664.84 MiB**; anonymous memory รวมสูงสุดประมาณ **499.13 MiB**
- CPU เฉลี่ยของแต่ละ service รวมประมาณ **0.134 core** ในช่วงโหลดอ่านข้อมูลขนาดเล็กนี้ ไม่ใช่การพิสูจน์ภายใต้ CPU quota 0.5 vCPU ของ Hostless
- ไม่พบ OOM kill ในการวัดนี้ และไม่มี source check ภายนอกเกิดขึ้น
- ผลสรุปคือ `production_fit_not_established` ไม่ใช่ free-hosting approval ยังไม่มี peak ingestion/optimizer/training หรือการตรวจ provider resource requests

ยังต้องวัดบน provider จริงด้วย dataset ขนาดที่รองรับ ภายใต้ CPU/RAM/storage requests ที่อนุญาต และตรวจ queue backpressure, abrupt crash recovery, ingestion/optimizer peaks, แผนสำรองข้อมูล และค่าใช้จ่าย การผ่าน CI baseline ไม่ยกเลิกข้อจำกัด PostgreSQL readiness/PostGIS ของ Hostless
