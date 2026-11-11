# วิธีคำนวณและข้อจำกัดทางวิทยาศาสตร์

ผลฉากทัศน์ถูกเก็บแยกตาม horizon, ขอบเขตพื้นที่, model version, validation policy และ fingerprint ของประวัติ Landsat ที่ publish แล้วเท่านั้น ไม่ใช้ข้อมูล staging หรือ synthetic แม้มีแถวอยู่ในตาราง observations การเพิ่ม backfill ย้อนหลังทำให้ fingerprint เปลี่ยนแม้ active map snapshot ไม่เปลี่ยน คำขอซ้ำใช้ผลเดิมและเก็บ run เก่าไว้ตรวจสอบ

งานเบื้องหลังตรวจ model update รายชั่วโมง แต่คำนวณชุดใหม่ไม่เกินวันละครั้งและข้ามเมื่อประวัติไม่เปลี่ยน horizon 1/3/5 ปีใช้ชุดเวอร์ชันเดียวกัน หากข้อมูลหรือ temporal/spatial validation ไม่ผ่าน จะเก็บผล `insufficient_data` พร้อม metrics และไม่สร้าง projection เพื่อเติมแผนที่ ช่วงความไม่แน่นอนยังเป็น empirical scenario range ตามสมมติฐานของแบบจำลอง ไม่ใช่การรับประกันความแม่นยำของคำพยากรณ์ห้าปี

## Heat Exposure Priority

คะแนน 0–100 เป็นลำดับความสำคัญด้านการสัมผัสความร้อน ไม่ใช่หลักฐาน socioeconomic inequality โดยอัตโนมัติ ตัวอย่างสูตรที่ตรวจสอบย้อนกลับได้:

```text
available_weight_sum = Σ weight_i for indicators that pass quality gates
score = 100 × Σ(weight_i × normalized_i) / available_weight_sum
```

Indicators ที่รองรับ:

- LST anomaly ภายในฤดูกาล/ช่วงเวลาเดียวกัน
- population exposure พร้อม reference year
- vegetation deficit (ทิศทางกลับของ NDVI/tree cover)
- built-up intensity
- historical change เมื่อมี observation ที่เปรียบเทียบกันได้
- verified vulnerability indicator เฉพาะเมื่อมี provenance/permission/definition ที่ชัดเจน

**การเลือกค่ารายเขต:** ภาพดาวเทียมแต่ละชุดครอบคลุมกรุงเทพฯ เพียงบางส่วนและมีเมฆบังบางเขต แต่ละเขตจึงใช้ค่าที่ผ่านการตรวจจากภาพล่าสุดที่ครอบคลุมเขตนั้นเอง โดยต้องเป็นเวอร์ชันที่เผยแพร่และมีวันที่สังเกตการณ์อยู่ภายในเกณฑ์อายุข้อมูลของแหล่ง (`stale_after_seconds` เช่น Landsat 45 วัน, Sentinel-2 21 วัน, Dynamic World 365 วัน เพราะสัดส่วนสิ่งปลูกสร้างและเรือนยอดไม้เปลี่ยนเป็นรายปี ไม่ใช่รายวัน และช่วงมรสุมใจกลางเมืองอาจไม่มีภาพที่เห็นพื้นติดต่อกันหลายเดือน เขตที่มีภาพใหม่กว่าใช้ภาพใหม่เสมอ ส่วนความเขียวรายฤดูยังมาจาก NDVI ของ Sentinel-2 อายุไม่เกิน 21 วัน) เวอร์ชันล่าสุดถูกรวมเสมอ ค่าที่ถูกตีตก (`rejected`/`invalid`) และค่าสาธิตไม่ถูกใช้ วันที่และ `dataset_version_id` ที่ป้อนแต่ละตัวแปรของแต่ละเขตถูกบันทึกใน `input_versions._area_observations` ของคะแนน ดังนั้นเขตต่างกันอาจอ้างอิงภาพคนละวันภายในช่วงอายุที่ยอมรับได้ ส่วน LST anomaly เทียบกับค่าเฉลี่ยของเขตทั้งหมดที่มีค่า ณ การคำนวณนั้น

Normalization parameters และ weight ต้อง versioned การขาดข้อมูลต้องลด `available_weight_sum` และตั้ง quality/confidence flag; ห้ามเติมค่าที่ขาดอย่างเงียบ ๆ

Sensitivity analysis ควรรัน weight perturbation และรายงาน rank stability เช่น percentile/range ของอันดับ ไม่ใช่แสดงคะแนนเป็นความจริงเชิงเด็ดขาด

## Land Surface Temperature

- LST เป็นอุณหภูมิพื้นผิวที่ sensor/model ประเมิน ณ เวลาผ่าน ไม่ใช่อุณหภูมิอากาศและไม่ใช่ค่าตลอดวัน
- เปรียบเทียบเฉพาะ observation ที่ cloud mask, season/time และ valid coverage เหมาะสม
- แสดง source observation time, collection, processing version, valid-pixel coverage และ uncertainty/quality
- mosaic/composite ต้องระบุช่วงวันที่; ห้ามใช้วัน ingest เป็น observation date

## Optimizer

Candidate ต้องมี geometry จริงและ feasibility ผ่าน rule ที่บันทึกไว้ Cost/effect assumptions มาจากตาราง versioned ที่ผู้มีอำนาจอนุมัติ หากไม่มี cost/benefit ที่รองรับให้ candidate เป็น `insufficient_evidence` และไม่ใส่ใน solver โดยอัตโนมัติ

Evidence gate ที่ใช้งานจริงแยกเป็นสองขั้น: (1) สร้างสมมติฐานต้นทุน `fixed_per_site` พร้อม version, วันที่มีผล และ URL เอกสารอ้างอิง (2) ผูกสมมติฐานนั้นกับ candidate ประเภทเดียวกันพร้อมรายงานหน้างาน และยืนยัน site feasibility, land control, design/accessibility review ครบทุกข้อ ค่า beneficiaries/benefit score เป็น optional และ backend จะไม่รับค่าดังกล่าวหากไม่มี benefit evidence URL

ตัวอย่าง objective แบบ linear:

```text
maximize Σ x_j × (
  w_beneficiary × normalized_exposed_population_j
  + w_priority × heat_priority_j
  + w_efficiency × defensible_benefit_per_baht_j
  + w_equity × verified_underserved_indicator_j
)
```

Subject to budget, eligibility, mutual-exclusion, capacity และ geographic coverage constraints. หากไม่มี verified underserved indicator ให้ weight ส่วนนั้นเป็นศูนย์และเปลี่ยนคำอธิบายให้ตรงหลักฐาน

Output ต้องรายงาน solver status, gap/time limit, input versions, allocated/unspent budget และเหตุผลของ candidate ที่ถูกเลือก/ไม่เลือก ผล A/B/C ต้องใช้ eligibility/cost/input version เดียวกัน

Implementation จัดสรรด้วย OR-Tools CP-SAT โดยงบรวมใช้บาทเต็มและปัดต้นทุนต่อจุดขึ้นเมื่อมีเศษบาท ค่า `enabled_types=[]` หรือ `candidate_ids=[]` หมายถึงไม่เลือกโครงการใด น้ำหนัก custom ที่ไม่ได้ระบุเป็นศูนย์ ไม่มีการเติมน้ำหนัก default แฝง ผลใหม่เก็บ geometry/cost/evidence snapshot ไว้กับ allocation; ผลเก่าที่ไม่มี snapshot จะแสดงข้อมูลไม่ครบ ไม่แทนด้วยรายละเอียดโครงการปัจจุบัน

Scenario A เรียงตาม LST ของ published snapshot ที่ใช้ร่วมกันแล้วเลือกโครงการที่งบพอ Scenario B เรียงตามประมาณการผู้รับประโยชน์ที่มีหลักฐานด้วยข้อจำกัดเดียวกัน Scenario C ใช้ OR-Tools น้ำหนักสมดุล คะแนน objective ในการเปรียบเทียบใช้สูตรสมดุลเดียวกันทุกพอร์ต หาก eligible project ใดไม่มีหลักฐานสำหรับการจัดอันดับ baseline นั้นจะเป็น `insufficient_data` ไม่ใช้ priority score แทนอุณหภูมิจริง และไม่ใช้ประชากรศูนย์แทนข้อมูลที่ขาด

จำนวนผู้รับประโยชน์รวมเป็นผลรวมประมาณการแต่ละโครงการ **ยังไม่หักซ้ำคนหรือพื้นที่บริการ** ไม่ใช่จำนวนคนไม่ซ้ำที่พิสูจน์แล้ว หากโครงการที่เลือกขาดประมาณการจะแสดงไม่ทราบแทนศูนย์ Saved scenarios เก็บ input fingerprint รวมรายละเอียด candidate; ต้นทุน/หลักฐานเปลี่ยนจะถูกทำเครื่องหมายล้าสมัยโดยไม่แทนผลเดิม

เพดานงบ optimizer ปัจจุบันเป็นเงินลงทุนเริ่มต้น (`estimated_cost_thb`) ไม่ใช่ lifecycle cost ระบบเก็บ `annual_maintenance_thb` และสมมติฐานแยก ไม่มีการสมมติปีอายุโครงการ อัตราคิดลด หรืองบดูแลที่ขาดหลักฐาน ผู้ตัดสินใจต้องจัดงบดูแลก่อนใช้เพื่ออนุมัติจริง

## Future outlook

เริ่มด้วย scenario-based analysis และ baseline trend ก่อนอ้างว่าเป็น forecast:

1. แบ่ง train/test ตามเวลา (ห้าม random split อย่างเดียว)
2. ทำ spatial holdout ระดับ district/grid block
3. เปรียบเทียบกับ persistence/seasonal-naive baseline
4. ตรวจ calibration/coverage ของ uncertainty interval
5. บันทึก training-data/model version และ assumptions

ถ้า model ไม่ชนะ baseline หรือช่วง uncertainty กว้างจนตัดสินใจไม่ได้ UI ต้องบอกตรง ๆ และแสดง scenario assumptions แทน “prediction map”

Implementation ปัจจุบันใช้ publication gate `scenario-v2` ดังนี้:

- รับเฉพาะ LST observation ที่ coverage ≥ 25%, อย่างน้อย 8 observation และอย่างน้อย 5 ปีต่อพื้นที่
- แบบจำลองเป็น linear trend + annual sine/cosine seasonality; ไม่อ้างว่ารวม climate forcing, land-use policy หรือ causal intervention effect
- temporal holdout ใช้ 20% ท้ายตามลำดับเวลาของทุกพื้นที่ และเทียบ seasonal-naive month baseline
- spatial holdout กัน district 20% แบบ deterministic ออกจากการ fit global change model และเทียบ no-change baseline
- ต้องมี holdout อย่างน้อย 20 samples ทั้งสองแบบ และ model ต้องชนะ baseline ทั้ง temporal และ spatial
- uncertainty ใช้ empirical 90th-percentile absolute holdout error และขยายตาม square-root ของ horizon; metric, input versions, assumptions และ model version ถูกเก็บใน `forecast_runs`

หาก gate ใดไม่ผ่าน endpoint จะบันทึก run เป็น `scenario_only`, ไม่สร้าง projection geometry และ UI จะแสดงเหตุผล/จำนวนปี/จำนวนพื้นที่ที่ยังขาด หากผ่าน ผลยังใช้ป้าย **validated historical trend scenario — not a forecast** เพราะไม่มีแบบจำลอง climate/land-use ภายนอกมารองรับคำพยากรณ์ห้าปี

## Intervention effects

ระบบห้ามสร้าง before/after temperature raster จากค่าทั่วไปที่ไม่มีการ validate เฉพาะพื้นที่ ผลจาก literature/model ต้องใช้คำว่า “projection” ระบุ transferability, maintenance, time horizon และ uncertainty
