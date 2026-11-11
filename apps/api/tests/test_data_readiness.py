from datetime import datetime, timezone

from sqlalchemy import select

from app.models.entities import (
    AreaIndicator, CandidateIntervention, CostAssumption, DataSource, DatasetVersion, ForecastRun, GeographicArea,
    PopulationEstimate, PriorityScore, RasterAssetMetadata, SourceCheck, TemperatureObservation, ValidationReport,
)
from app.services.data_readiness import published_data_readiness
from app.services.forecast_workflow import forecast_inputs
from app.services.versions import INDICATOR_DATASETS, LAYER_DATASETS


def test_empty_install_is_not_mistaken_for_real_data_readiness(client):
    response = client.get("/api/system/data-readiness")
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-cache"
    report = response.json()
    assert report["data_ready"] is False
    assert report["coverage"]["complete_pilot_areas"] == 0
    assert report["future_scenarios_available"] is False
    assert report["external_verification_required_th"]
    assert "production_ready" not in report


def _recorded_pilot(db):
    """Synthetic records in a disposable unit-test database, never provider data."""
    now = datetime.now(timezone.utc)
    versions = {}
    for source in db.scalars(select(DataSource).where(DataSource.source_type != "test_fixture")):
        version = DatasetVersion(source_id=source.id, source_asset_id="unit-audit-" + source.id, source_version="unit-test-only", status="published", quality_status="valid", observed_at=now)
        db.add(version); db.flush()
        versions[source.dataset_id] = version
        db.add(ValidationReport(dataset_version_id=version.id, passed=True, checks={}, warnings=[], errors=[]))
        db.add(SourceCheck(source_id=source.id, checked_at=now, completed_at=now, status="success"))
    db.add(GeographicArea(id="unit-pilot", name_th="พื้นที่จำลองสำหรับ unit test", name_en="Synthetic unit-test area", quality_status="verified_official_boundary", geometry={"type": "Polygon", "coordinates": [[[100.5,13.7],[100.51,13.7],[100.51,13.71],[100.5,13.71],[100.5,13.7]]]}))
    for key, value in {"lst_c_mean": 35, "ndvi": .5, "tree_cover_fraction": 45, "built_fraction": 60, "population_density": 9000}.items():
        db.add(AreaIndicator(area_id="unit-pilot", dataset_version_id=versions[INDICATOR_DATASETS[key]].id, indicator_key=key, value=value, observed_at=now, quality_status="model_estimate" if key == "population_density" else "valid"))
    for key in ("temperature", "ndvi", "vegetation"):
        db.add(RasterAssetMetadata(dataset_version_id=versions[LAYER_DATASETS[key]].id, storage_uri="earthengine://unit-test-only", media_type="application/vnd.google-earth-engine.image", crs="EPSG:4326", bounds={}, band_metadata={}))
    db.add(PopulationEstimate(area_id="unit-pilot", dataset_version_id=versions[LAYER_DATASETS["population"]].id, reference_year=now.year, population=1000))
    db.add(PriorityScore(area_id="unit-pilot", score=50, components={}, weights={}, missing_inputs=[], input_versions={dataset: {"version_id": version.id} for dataset, version in versions.items()}))
    cost = CostAssumption(intervention_type="shade_structure", version="unit-only", evidence_url="https://example.org/unit-cost", effective_from=now, assumptions={"status": "approved", "unit_cost_thb": 100000})
    db.add(cost); db.flush()
    candidate = CandidateIntervention(id="unit-site", area_id="unit-pilot", name_th="จุดจำลอง", intervention_type="shade_structure", geometry={"type":"Point","coordinates":[100.5,13.7]}, estimated_cost_thb=100000, priority_score=50,
        feasibility_status="eligible", feasibility_constraints={"site_survey_completed": True, "land_control_verified": True, "design_accessibility_reviewed": True},
        evidence={"cost_assumption_id":cost.id,"approved_by":"synthetic-ci-admin","feasibility_evidence":"https://example.org/unit-survey"})
    db.add(candidate); db.commit()
    return versions, candidate


def test_complete_recorded_pilot_can_pass_data_checks_without_claiming_operational_or_forecast_approval(db):
    _recorded_pilot(db)
    report = published_data_readiness(db)
    assert report["data_ready"] is True
    assert report["coverage"] == {"official_areas": 1, "complete_pilot_areas": 1, "evidence_backed_candidates": 1,
                                  "desk_reviewed_candidates": 0}
    assert report["future_scenarios_available"] is False
    assert any(c["code"].startswith("license.") and c["status"] == "warning" for c in report["checks"])
    assert report["external_verification_required_th"]


def test_synthetic_active_input_or_missing_feasibility_blocks_decision_readiness(db):
    versions, candidate = _recorded_pilot(db)
    versions[LAYER_DATASETS["temperature"]].metadata_json = {"fixture": True}
    candidate.feasibility_constraints = {"site_survey_completed": False}
    db.commit()
    report = published_data_readiness(db)
    assert report["data_ready"] is False and report["coverage"]["complete_pilot_areas"] == 0
    assert report["coverage"]["evidence_backed_candidates"] == 0


def test_new_history_marks_a_previously_validated_scenario_as_outdated(db):
    versions, _ = _recorded_pilot(db)
    version_id = versions[LAYER_DATASETS["temperature"]].id
    db.add(TemperatureObservation(area_id="unit-pilot", dataset_version_id=version_id,
        observed_at=datetime(2020, 1, 1, tzinfo=timezone.utc), lst_c_mean=32, coverage_fraction=.9))
    db.commit()
    inputs, _ = forecast_inputs(db)
    run = ForecastRun(horizon_years=5, method="synthetic-unit-validation-only", model_version="scenario-v2",
        training_versions=inputs, assumptions={}, metrics={"validation_passed": True}, results={"status": "completed"})
    db.add(run); db.commit()
    assert published_data_readiness(db)["future_scenarios_available"] is True
    db.add(TemperatureObservation(area_id="unit-pilot", dataset_version_id=version_id,
        observed_at=datetime(2021, 1, 1, tzinfo=timezone.utc), lst_c_mean=33, coverage_fraction=.9))
    db.commit()
    assert published_data_readiness(db)["future_scenarios_available"] is False
    assert db.get(ForecastRun, run.id).training_versions == inputs


def test_missing_site_evidence_warns_without_blocking_published_data(db):
    _, candidate = _recorded_pilot(db)
    assert published_data_readiness(db)["investment_decisions_ready"] is True
    candidate.feasibility_constraints = {"site_survey_completed": False}
    db.commit()
    report = published_data_readiness(db)
    investment = next(c for c in report["checks"] if c["code"] == "investment.evidence")
    assert investment["status"] == "warning" and "ประมาณการเบื้องต้น" in investment["detail_th"]
    assert report["data_ready"] is True and report["investment_decisions_ready"] is False


def test_unreachable_boundary_provider_warns_while_validated_boundaries_stay_published(db):
    from app.models.entities import DataSource, SourceCheck

    _recorded_pilot(db)
    boundary = db.scalar(select(DataSource).where(DataSource.source_type == "boundary"))
    db.add(SourceCheck(source_id=boundary.id, checked_at=datetime.now(timezone.utc), status="failed",
                       error_message="BMA boundary catalog request failed: [Errno 104] Connection reset by peer"))
    db.commit()
    report = published_data_readiness(db)
    check = next(c for c in report["checks"] if c["code"] == "check.bma-boundaries")
    assert check["status"] == "warning" and "Connection reset" in check["detail_th"]
    assert report["data_ready"] is True

    # Other sources keep blocking when their latest provider check fails.
    weather = db.scalar(select(DataSource).where(DataSource.source_type == "weather"))
    db.add(SourceCheck(source_id=weather.id, checked_at=datetime.now(timezone.utc), status="failed"))
    db.commit()
    assert published_data_readiness(db)["data_ready"] is False


def test_desk_reviewed_site_counts_as_evidence_with_onsite_caveat(db):
    _, candidate = _recorded_pilot(db)
    candidate.feasibility_constraints = {"site_survey_completed": False, "remote_site_review": True,
                                         "land_control_verified": True, "design_accessibility_reviewed": True}
    db.commit()
    report = published_data_readiness(db)
    assert report["investment_decisions_ready"] is True
    assert report["coverage"]["desk_reviewed_candidates"] == 1
    check = next(c for c in report["checks"] if c["code"] == "investment.evidence")
    assert check["status"] == "ready" and "ยืนยันหน้างาน" in check["detail_th"]
