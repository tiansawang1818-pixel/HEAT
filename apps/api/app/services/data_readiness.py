"""Audit published data and decision evidence; never infer deployment approval."""
from datetime import datetime, timezone

from sqlalchemy import desc, func, select

from app.models.entities import (
    AreaIndicator, CandidateIntervention, CostAssumption, DataSource, DatasetVersion,
    ForecastRun, GeographicArea, PopulationEstimate, PriorityScore, RasterAssetMetadata, SourceCheck, ValidationReport,
)
from app.services.source_registry import SOURCE_KEYS
from app.services.versions import INDICATOR_DATASETS, LAYER_DATASETS, indicator_version_pool, published_version
from app.services.forecast_workflow import forecast_inputs


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def published_data_readiness(db):
    now = datetime.now(timezone.utc)
    checks, versions = [], {}

    def add(code, ready, label, detail, *, warning=False):
        checks.append({"code": code, "status": "warning" if warning else "ready" if ready else "blocked", "label_th": label, "detail_th": detail})

    sources = {source.dataset_id: source for source in db.scalars(select(DataSource).where(DataSource.dataset_id.in_(SOURCE_KEYS)))}
    for dataset_id, source_key in SOURCE_KEYS.items():
        source = sources.get(dataset_id)
        if source is None:
            add("source." + source_key, False, source_key, "ยังไม่มี source configuration ในฐานข้อมูล")
            continue
        add("monitoring." + source_key, source.enabled, "การติดตามอัตโนมัติ: " + source.name, "เปิดการติดตามอัตโนมัติแล้ว" if source.enabled else "แหล่งข้อมูลนี้ยังปิดการติดตามอัตโนมัติ")
        version = published_version(db, source.dataset_id)
        scientific = bool(version and version.quality_status in {"valid", "verified"}
                          and not (version.metadata_json or {}).get("fixture") and (version.metadata_json or {}).get("scientific_use") is not False)
        report = db.scalar(select(ValidationReport).where(ValidationReport.dataset_version_id == version.id).order_by(desc(ValidationReport.created_at)).limit(1)) if version else None
        valid = scientific and report is not None and report.passed
        add("source." + SOURCE_KEYS[source.dataset_id], valid, source.name,
            "มีเวอร์ชันจริงที่เผยแพร่และมี validation report ผ่าน" if valid else "ยังไม่มีเวอร์ชันจริงที่ผ่าน validation และเผยแพร่พร้อมรายงาน")
        if not valid:
            continue
        versions[source.dataset_id] = version
        last_check = db.scalar(select(SourceCheck).where(SourceCheck.source_id == source.id).order_by(desc(SourceCheck.checked_at)).limit(1))
        checked = bool(last_check and last_check.status == "success" and (now - _aware(last_check.checked_at)).total_seconds() <= max(120, 2 * source.check_interval_seconds))
        if not checked and source.source_type == "boundary" and version.status == "published":
            # District boundaries change rarely. When the provider cannot be
            # reached, the last validated official geometry stays published, so
            # the failed check is a warning rather than a data blocker.
            add("check." + SOURCE_KEYS[source.dataset_id], False, "การตรวจล่าสุด: " + source.name,
                "ต่อแหล่งข้อมูลจากเซิร์ฟเวอร์นี้ไม่สำเร็จ"
                + (" (%s)" % last_check.error_message[:120] if last_check and last_check.error_message else "")
                + " ยังใช้ขอบเขตทางการที่ผ่านการตรวจล่าสุด ระบบลองใหม่ทุกชั่วโมง", warning=True)
        else:
            add("check." + SOURCE_KEYS[source.dataset_id], checked, "การตรวจล่าสุด: " + source.name,
                "ตรวจแหล่งข้อมูลสำเร็จภายในรอบที่กำหนด" if checked else "ต้องยืนยันการตรวจ provider ล่าสุดและ scheduler บนระบบนี้")
        if not version.observed_at:
            add("date." + SOURCE_KEYS[source.dataset_id], False, "วันที่ต้นทาง: " + source.name, "ไม่พบวันที่ต้นทางที่ตรวจสอบได้")
        elif (now - _aware(version.observed_at)).total_seconds() > source.stale_after_seconds:
            add("freshness." + SOURCE_KEYS[source.dataset_id], False, "อายุข้อมูล: " + source.name, "ข้อมูลเกินเกณฑ์อายุ ต้องคงป้ายข้อมูลเก่าและทบทวนความเหมาะสม", warning=True)
        metadata = version.metadata_json or {}
        license_name = str(metadata.get("license") or "").strip()
        if not source.license_url and (not license_name or "not specified" in license_name.lower()):
            add("license." + SOURCE_KEYS[source.dataset_id], False, "สิทธิ์ใช้ข้อมูล: " + source.name, "metadata ไม่ยืนยันเงื่อนไขสิทธิ์ ต้องตรวจหลักฐานก่อนเปิดเผยข้อมูล", warning=True)

    official_ids = set(db.scalars(select(GeographicArea.id).where(GeographicArea.quality_status == "verified_official_boundary")))
    official_count = len(official_ids)
    add("coverage.boundaries", official_count > 0, "ขอบเขตพื้นที่ทางการ", f"มีขอบเขตที่ตรวจแล้ว {official_count} พื้นที่")
    coverage = []
    for indicator in ("lst_c_mean", "ndvi", "tree_cover_fraction", "built_fraction", "population_density"):
        version = versions.get(INDICATOR_DATASETS[indicator])
        pool = indicator_version_pool(db, INDICATOR_DATASETS[indicator], now) if version else ["missing"]
        rows = db.scalars(select(AreaIndicator.area_id).where(
            AreaIndicator.indicator_key == indicator,
            AreaIndicator.dataset_version_id.in_(pool),
            AreaIndicator.value.is_not(None), AreaIndicator.quality_status.notin_(["synthetic_demo", "demo_only", "test_only", "rejected", "invalid"]),
        )).all()
        coverage.append(set(rows))
    complete_areas = set.intersection(*coverage) & official_ids if coverage else set()
    add("coverage.pilot", bool(complete_areas), "พื้นที่นำร่องที่มีข้อมูลครบ", f"มี {len(complete_areas)} พื้นที่ที่มีอุณหภูมิ NDVI พืชพรรณ สิ่งปลูกสร้าง และประชากรจากภาพที่ใช้ได้ล่าสุดของพื้นที่นั้นภายในเกณฑ์อายุข้อมูล")
    population = versions.get(INDICATOR_DATASETS["population_density"])
    population_count = db.scalar(select(func.count()).select_from(PopulationEstimate).where(
        PopulationEstimate.dataset_version_id == (population.id if population else "missing"),
        PopulationEstimate.reference_year <= now.year, PopulationEstimate.population >= 0,
    )) or 0
    add("population.reference_year", population_count > 0, "ปีอ้างอิงประชากร", "มี population estimates พร้อมปีอ้างอิงที่ไม่ใช่อนาคต" if population_count else "ต้องมี population estimates และปีอ้างอิงจริง")
    raster_ids = set(db.scalars(select(RasterAssetMetadata.dataset_version_id).where(
        RasterAssetMetadata.dataset_version_id.in_([v.id for v in versions.values()]), RasterAssetMetadata.crs == "EPSG:4326",
    )))
    expected_rasters = {versions[dataset].id for dataset in {LAYER_DATASETS[k] for k in ("temperature", "ndvi", "vegetation")} if dataset in versions}
    add("map.raster_metadata", len(expected_rasters) == 3 and expected_rasters <= raster_ids, "ที่มาของ raster layers", f"พบ metadata WGS84 สำหรับ {len(raster_ids)} เวอร์ชัน; ยังต้องตรวจภาพ tiles กับพื้นที่จริง")
    scores = db.scalars(select(PriorityScore).where(PriorityScore.area_id.in_(complete_areas)).order_by(desc(PriorityScore.calculated_at))).all()
    current_score = False
    seen = set()
    for score in scores:
        if score.area_id in seen:
            continue
        seen.add(score.area_id)
        if all((score.input_versions.get(dataset) or {}).get("version_id") == versions[dataset].id for dataset in set(INDICATOR_DATASETS.values()) if dataset in versions):
            current_score = True
    add("priority.current_inputs", current_score, "คะแนนอ้างอิงข้อมูลปัจจุบัน", "มีคะแนนของพื้นที่นำร่องที่อ้างอิงเวอร์ชันปัจจุบัน" if current_score else "ยังไม่มีคะแนนที่ตรวจย้อนกลับไปยังข้อมูลนำร่องชุดปัจจุบันได้")
    eligible = desk_reviewed = 0
    for candidate in db.scalars(select(CandidateIntervention).where(CandidateIntervention.active.is_(True), CandidateIntervention.feasibility_status == "eligible")):
        evidence = candidate.evidence or {}
        cost = db.get(CostAssumption, evidence.get("cost_assumption_id")) if evidence.get("cost_assumption_id") else None
        feasibility = candidate.feasibility_constraints or {}
        if (candidate.area_id in complete_areas and candidate.estimated_cost_thb and candidate.estimated_cost_thb > 0
                and cost and cost.assumptions.get("status") == "approved" and cost.evidence_url
                and cost.intervention_type == candidate.intervention_type and _aware(cost.effective_from) <= now
                and cost.assumptions.get("unit_cost_thb") == candidate.estimated_cost_thb
                and evidence.get("approved_by") and evidence.get("feasibility_evidence")
                and (feasibility.get("site_survey_completed") is True or feasibility.get("remote_site_review") is True)
                and all(feasibility.get(key) is True for key in ("land_control_verified", "design_accessibility_reviewed"))):
            eligible += 1
            desk_reviewed += feasibility.get("site_survey_completed") is not True
    # Recorded site evidence gates investment decisions, not published data:
    # without it the optimizer still funds nothing, so this stays a warning.
    add("investment.evidence", eligible > 0, "โครงการพร้อมหลักฐาน",
        f"มี {eligible} โครงการในพื้นที่นำร่องที่มีหลักฐานต้นทุนและความเหมาะสมที่ผู้ดูแลบันทึกแล้ว"
        + (f" (ตรวจจากระยะไกล {desk_reviewed} จุด ต้องยืนยันหน้างานก่อนก่อสร้าง)" if desk_reviewed else "")
        + ("" if eligible else " ข้อมูลพร้อมใช้ แต่การตัดสินใจลงทุนจริงยังต้องมีหลักฐานรายจุด ระหว่างนี้ใช้ได้เฉพาะการคาดการณ์พอร์ต"),
        warning=eligible == 0)
    latest_model = db.scalar(select(ForecastRun).order_by(desc(ForecastRun.created_at)).limit(1))
    current_model = bool(latest_model and latest_model.results.get("status") == "completed" and latest_model.metrics.get("validation_passed"))
    if current_model:
        expected_history = forecast_inputs(db)[0]["history"]
        current_model = expected_history["observation_count"] > 0 and (latest_model.training_versions.get("history") or {}).get("fingerprint") == expected_history["fingerprint"]
    return {
        "scope": "published_data_and_recorded_decision_evidence", "data_ready": all(c["status"] != "blocked" for c in checks),
        "investment_decisions_ready": eligible > 0,
        "generated_at": now.isoformat(), "checks": checks,
        "summary": {status: sum(c["status"] == status for c in checks) for status in ("ready", "warning", "blocked")},
        "coverage": {"official_areas": official_count, "complete_pilot_areas": len(complete_areas), "evidence_backed_candidates": eligible, "desk_reviewed_candidates": desk_reviewed},
        "published_versions": {dataset: {"version_id": v.id, "observed_at": _aware(v.observed_at).isoformat() if v.observed_at else None} for dataset, v in versions.items()},
        "future_scenarios_available": current_model,
        "external_verification_required_th": [
            "ตรวจ SSO ด้วยบัญชีผู้ดูแลจริง รวมการปฏิเสธบัญชีที่ไม่มีสิทธิ์",
            "ตรวจ DNS, HTTPS, ภาพ raster และขอบเขตพื้นที่บนเซิร์ฟเวอร์จริง",
            "ตรวจใบอนุญาตแผนที่และข้อมูลตามรูปแบบการใช้งานของโครงการ",
            "ซ้อมกู้คืนจากปลายทางสำรองจริง ทดสอบ alert delivery และโหลดที่คาดการณ์",
            "ทบทวนหลักฐานต้นทุน การสำรวจพื้นที่ และข้อจำกัดทางวิทยาศาสตร์โดยผู้รับผิดชอบ",
        ],
        "limitations_th": "ผลนี้ตรวจระเบียนข้อมูลและหลักฐานที่บันทึกในระบบ ไม่รับรองสิทธิ์ใช้งาน ความถูกต้องหน้างาน หรือความพร้อม production ทั้งระบบ",
    }
